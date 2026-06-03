from __future__ import annotations

import json
import time
import uuid
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.config import settings
from app.schemas import ChatAttachment, ToolCallRecord
from app.services.model_client import ModelClient
from app.services.persona import builder as persona_builder
from app.tools.registry import ToolRegistry
from app.tools.result_status import infer_tool_status

# An optional async callback the orchestrator can install to receive
# ``tool_start`` / ``tool_end`` progress events. Each event is a dict with
# ``event`` (str) and ``data`` (dict) keys, mirroring the SSE wire format.
ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


def _new_step_id(index: int) -> str:
    """Stable-but-unique step identifier for correlating start/end events.

    The numeric prefix keeps logs readable; the random suffix prevents
    collisions when multiple turns share state in the renderer.
    """
    return f"step-{index}-{uuid.uuid4().hex[:6]}"


# Heuristic: only do a plan-first turn for messages that look like multi-step
# work. A one-line greeting or a "what time is it" doesn't need a plan and
# spending an extra LLM call on it would be silly. Keep this conservative and
# err on the side of skipping — the user-facing failure mode of "no plan
# rendered" is much better than "every chat costs 2x".
_PLAN_KEYWORDS = (
    "帮我", "实现", "修复", "测试", "部署", "下载", "安装", "整理",
    "脚本", "项目", "调用", "运行", "查找", "改", "build", "install",
    "deploy", "test", "script", "fix", "implement", "refactor", "search",
    "调研", "排查", "分析", "对比",
)


def _should_plan_first(message: str) -> bool:
    """True if the user message looks like a multi-step task worth planning."""
    if not settings.enable_plan_first:
        return False
    text = message.strip()
    if len(text) < 12:
        return False
    lowered = text.lower()
    return any(keyword in lowered for keyword in _PLAN_KEYWORDS)


_PLAN_PROMPT_SUFFIX = (
    "\n\n[planning] 用户的请求看起来是多步任务。"
    "请在动手调用任何工具之前先返回一个简短的执行计划。"
    "用严格 JSON：{\"plan\": [\"步骤1\", \"步骤2\", ...], \"summary\": \"一句话概括\"}。"
    "每个步骤一句话，最多 6 步。"
    "如果你判断这其实是个简单问题不需要计划，返回 {\"plan\": [], \"summary\": \"\"} 即可。"
    "只返回 JSON 本体，不要 ```json 包裹。"
)


def _safe_parse_plan(raw: str) -> dict | None:
    """Extract a ``{"plan": [...], "summary": "..."}`` dict from a model
    reply, tolerating ``json``-fenced blocks and trailing prose.

    Strategy: ignore everything outside the first balanced ``{...}`` span.
    This handles triple-backtick json fences, leading explanations like
    "Here is the plan:", and trailing commentary in one shot.
    """
    if not raw:
        return None
    text = raw.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = text[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    plan = parsed.get("plan")
    if not isinstance(plan, list):
        return None
    cleaned_plan = [str(step).strip() for step in plan if str(step).strip()]
    return {"plan": cleaned_plan[:6], "summary": str(parsed.get("summary") or "").strip()}


class LLMAgent:
    name = "base-agent"

    def __init__(self) -> None:
        self.model_client = ModelClient()
        # Dedicated vision client (purpose="vision" → settings.vision_provider
        # / vision_model). Having it on the base class lets *any* agent handle
        # an image gracefully even when its main chat model is text-only — see
        # the fallback at the top of ``handle``.
        self.vision_client = ModelClient(purpose="vision")

    def _vision_unavailable_reply(self) -> str:
        return (
            "当前视觉模型暂时不可用。请检查 VISION_PROVIDER、VISION_MODEL 和对应 API key。"
        )

    async def _handle_image_attachments(
        self,
        message: str,
        attachments: list[ChatAttachment],
        memory_summary: str,
    ) -> tuple[str, list[ToolCallRecord]]:
        """Describe/answer about image attachments via the vision client.

        Used when an agent is handed an image but its main chat model can't
        see — so the image still gets understood instead of erroring out.
        """
        if not self.vision_client.supports_vision():
            return self._vision_unavailable_reply(), []

        messages = self.build_messages(message, attachments, memory_summary)
        try:
            response = await self.vision_client.chat(messages, tools=None)
            reply = self.vision_client.extract_text(response).strip()
        except Exception as exc:
            reply = f"图片分析暂时失败：{exc}"
        return reply or "我看到了图片，但当前视觉模型没有返回有效描述。", []

    def get_system_prompt(self) -> str:
        """Build the system prompt for this turn.

        Subclasses pass their canonical agent name to :class:`PersonaBuilder`
        so the persona body (name / traits / style / …) stays consistent
        across companion/desktop/terminal while each agent still gets its own
        role-specific addendum (tool guidance, screen-observation rules, …).

        Reading from the builder on every call means a /api/settings PUT
        flips the persona without restarting any process.
        """
        return persona_builder.render(self.name)

    def build_messages(
        self,
        message: str,
        attachments: list[ChatAttachment],
        memory_summary: str,
    ) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    f"Memory summary: {memory_summary}\n"
                    f"{self._runtime_context()}\n"
                    f"User request: {message}"
                ),
            }
        ]
        inline_images = 0
        path_only_images = 0
        for attachment in attachments:
            if attachment.kind != "image":
                continue
            if attachment.data_url:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": attachment.data_url},
                    }
                )
                inline_images += 1
            elif attachment.path:
                path_only_images += 1

        if inline_images == 0 or path_only_images > 0:
            content[0]["text"] += (
                f"\nImage attachments summary: inline_images={inline_images}, path_only_images={path_only_images}."
            )

        return [
            {"role": "system", "content": self.get_system_prompt()},
            {"role": "user", "content": content},
        ]

    async def compose_line(self, instruction: str, context: str = "") -> str:
        """Single-shot, tool-free generation in this agent's persona.

        Used for short proactive companion lines (a greeting, a memory
        call-back, a light news mention). Returns ``""`` on any model error so
        callers can fall back to a template.
        """
        user_text = f"{context}\n\n{instruction}".strip() if context else instruction
        messages = [
            {"role": "system", "content": self.get_system_prompt()},
            {"role": "user", "content": user_text},
        ]
        try:
            response = await self.model_client.chat(messages, tools=None)
            return self.model_client.extract_text(response).strip()
        except Exception:  # pragma: no cover — defensive; callers fall back
            return ""

    async def handle(
        self,
        message: str,
        registry: ToolRegistry,
        attachments: list[ChatAttachment],
        memory_summary: str,
        session_id: str,
        progress_cb: ProgressCallback | None = None,
    ) -> tuple[str, list[ToolCallRecord]]:
        """Run a chat turn with tool calling.

        ``progress_cb`` is an optional async callback the orchestrator can
        install to receive per-tool ``tool_start`` / ``tool_end`` events for
        live progress rendering. The callback never blocks the tool itself —
        events are best-effort, and if the callback raises we swallow the
        exception so a buggy listener cannot kill the agent loop.
        """
        # Image attachments + a text-only chat model: route to the vision
        # client instead of erroring out. This makes companion/terminal agents
        # robust to being handed an image — e.g. the local router sent an
        # image-bearing message here because of a strong tool keyword, even
        # though the main model can't see. If the chat model *does* support
        # vision, fall through and let the image ride the normal tool loop.
        if attachments and not self.model_client.supports_vision():
            return await self._handle_image_attachments(message, attachments, memory_summary)

        messages = self.build_messages(message, attachments, memory_summary)
        tool_calls: list[ToolCallRecord] = []

        async def _emit(event: str, payload: dict[str, Any]) -> None:
            if progress_cb is None:
                return
            try:
                await progress_cb({"event": event, "data": payload})
            except Exception:  # pragma: no cover — protect the agent loop
                pass

        # Plan-first: for multi-step requests, ask the model up front for a
        # short JSON plan and surface it as a SSE ``plan`` event so the UI can
        # render a checklist that ticks off as steps land. Keep the planning
        # call out-of-band of ``messages`` — we don't want the plan to leak
        # into the tool-calling history and confuse the model on the next
        # turn. Failure to produce a parseable plan is silent; we just skip
        # the event and let the regular flow run.
        if progress_cb is not None and _should_plan_first(message):
            await self._generate_plan(messages, _emit)

        # Iteration cap is now configurable. Long coding tasks need more
        # round-trips than a chat reply (6 was tight); the user can lower it
        # from the settings UI if they're cost-sensitive.
        max_iterations = max(1, int(settings.max_tool_iterations))
        try:
            for _ in range(max_iterations):
                response = await self.model_client.chat(messages, tools=registry.specs())
                assistant_message = self.model_client.extract_message(response)
                raw_tool_calls = assistant_message.get("tool_calls") or []

                if raw_tool_calls:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": assistant_message.get("content") or "",
                            "tool_calls": raw_tool_calls,
                        }
                    )
                    for tool_call in raw_tool_calls:
                        name = tool_call["function"]["name"]
                        step_index = len(tool_calls) + 1
                        step_id = _new_step_id(step_index)
                        raw_args = tool_call["function"].get("arguments") or "{}"
                        try:
                            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                        except JSONDecodeError as exc:
                            args = {}
                            result = f"Tool {name} failed: invalid JSON arguments: {exc}"
                            await _emit(
                                "tool_start",
                                {"step_id": step_id, "index": step_index, "name": name, "args": args},
                            )
                            record = ToolCallRecord(
                                name=name, args=args, result=result, success=False,
                                step_id=step_id, duration_ms=0,
                            )
                            tool_calls.append(record)
                            await _emit(
                                "tool_end",
                                {
                                    "step_id": step_id,
                                    "index": step_index,
                                    "name": name,
                                    "args": args,
                                    "result": result,
                                    "success": False,
                                    "duration_ms": 0,
                                },
                            )
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tool_call["id"],
                                    "name": name,
                                    "content": result,
                                }
                            )
                            continue
                        if name.startswith("terminal."):
                            args.setdefault("session_id", session_id)
                        await _emit(
                            "tool_start",
                            {"step_id": step_id, "index": step_index, "name": name, "args": args},
                        )
                        started_at = time.perf_counter()
                        success = True
                        try:
                            result = await registry.arun(name, args)
                            # Registry returns a sentinel for unknown tools; surface that as failure.
                            if not registry.has(name):
                                success = False
                        except Exception as exc:
                            result = f"Tool {name} failed: {exc}"
                            success = False
                        if infer_tool_status(result, success=success) != "completed":
                            success = False
                        duration_ms = int((time.perf_counter() - started_at) * 1000)
                        record = ToolCallRecord(
                            name=name, args=args, result=result, success=success,
                            step_id=step_id, duration_ms=duration_ms,
                        )
                        tool_calls.append(record)
                        await _emit(
                            "tool_end",
                            {
                                "step_id": step_id,
                                "index": step_index,
                                "name": name,
                                "args": args,
                                "result": result,
                                "success": success,
                                "duration_ms": duration_ms,
                            },
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call["id"],
                                "name": name,
                                "content": result,
                            }
                        )
                    continue

                reply = self.model_client.extract_text(response).strip()
                if reply:
                    return reply, tool_calls
        except ValueError as exc:
            if tool_calls:
                return f"我完成了部分操作，但当前配置不支持这次图像输入：{exc}", tool_calls
            return f"当前配置不支持这次图像输入：{exc}", tool_calls
        except Exception as exc:
            if tool_calls:
                return f"我完成了部分操作，但模型服务在生成最终回复时出错：{exc}", tool_calls
            return f"模型服务暂时不可用，我先保留任务上下文。错误信息：{exc}", tool_calls

        if tool_calls:
            return self._summarize_tool_fallback(tool_calls), tool_calls
        return "我已经完成分析，但当前模型没有给出最终自然语言回复。", tool_calls

    async def _generate_plan(
        self,
        messages: list[dict[str, Any]],
        emit: Callable[[str, dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Ask the model for a multi-step plan and emit it as a ``plan`` event.

        Failures here are non-fatal — bad JSON, network glitches, etc. all
        downgrade to "no plan rendered" and the regular tool loop runs as
        before. We deliberately use a side-channel ``messages`` copy so the
        plan response doesn't pollute the conversation history.
        """
        # Branch off a copy so the actual tool-call history below stays clean.
        plan_messages = [dict(m) for m in messages]
        # The system prompt is the first message — append the planning ask
        # to it rather than to the user message; that's where the model
        # expects format directives.
        if plan_messages and plan_messages[0].get("role") == "system":
            plan_messages[0] = {
                "role": "system",
                "content": str(plan_messages[0].get("content", "")) + _PLAN_PROMPT_SUFFIX,
            }
        try:
            response = await self.model_client.chat(plan_messages, tools=None)
            text = self.model_client.extract_text(response).strip()
        except Exception:
            return
        parsed = _safe_parse_plan(text)
        if not parsed or not parsed["plan"]:
            return
        await emit("plan", parsed)

    @staticmethod
    def _summarize_tool_fallback(tool_calls: list[ToolCallRecord]) -> str:
        lines = ["工具调用已结束，下面是执行结果摘要："]
        for index, tool_call in enumerate(tool_calls[-4:], start=1):
            detail = " ".join(tool_call.result.split())
            lines.append(f"{index}. {tool_call.name}: {detail[:260]}")
        return "\n".join(lines)

    @staticmethod
    def _runtime_context() -> str:
        cwd = Path.cwd()
        project_root = cwd.parent if cwd.name == "agent-core" else cwd
        desktop_dir = project_root / "desktop"
        return (
            f"Current backend working directory: {cwd}\n"
            f"Project root: {project_root}\n"
            f"Agent core directory: {project_root / 'agent-core'}\n"
            f"Desktop app directory: {desktop_dir if desktop_dir.exists() else 'not found'}"
        )
