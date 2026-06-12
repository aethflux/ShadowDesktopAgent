from __future__ import annotations

import asyncio
import difflib
import json
from pathlib import Path
import re
import time
from typing import AsyncIterator

from app.agents.companion import CompanionAgent
from app.agents.desktop import DesktopAgent
from app.agents.planner import PlannerAgent
from app.agents.router import RouterAgent
from app.agents.terminal_agent import TerminalAgent
from app.config import settings
from app.logging import get_logger
from app.schemas import (
    AgentTrace,
    ChatRequest,
    ChatResponse,
    ChatterRequest,
    ChatterResponse,
    IntentMatch,
    MemoryItem,
    ObservationRequest,
    ObservationResponse,
    ToolCallRecord,
    UserProfile,
    VoiceTTSRequest,
    VoiceTTSResponse,
)
from app.services import news
from app.services.companion_strategy import CompanionStrategy
from app.services.context_manager import ContextManager
from app.services.mcp_client import MCPClient
from app.services.memory import MemoryStore
from app.services.skill_loader import SkillLoader
from app.services.streaming_context import progress_cb_var, session_id_var
from app.tools.registry import ToolRegistry
from app.tools.result_status import infer_tool_status

logger = get_logger("orchestrator")


def _chunk_text(text: str, size: int) -> list[str]:
    """Split a string into roughly fixed-size chunks for SSE delta events.

    Returns at least one chunk for non-empty input (so the consumer always
    sees the reply, even if it's shorter than ``size``).
    """
    if not text:
        return []
    if size <= 0:
        return [text]
    return [text[i : i + size] for i in range(0, len(text), size)]


_OBSERVATION_TEXT_NOISE_RE = re.compile(
    r"[\s\u200b\ufeff`*_#>\-—–~!！?？,，.。:：;；、\"“”'‘’()\[\]{}<>《》/\\|]+"
)
_OBSERVATION_EMOJI_RE = re.compile(r"[\U00010000-\U0010ffff]")
_OBSERVATION_LATIN_TERM_RE = re.compile(r"[a-z0-9]{3,}")


def _observation_core(text: str) -> str:
    """Return the actual observation part, dropping prepended nudge text."""
    parts = [part.strip() for part in re.split(r"\n{2,}", text.strip()) if part.strip()]
    return parts[-1] if parts else text.strip()


def _normalize_observation_reply(text: str) -> str:
    text = _OBSERVATION_EMOJI_RE.sub("", text.lower())
    return _OBSERVATION_TEXT_NOISE_RE.sub("", text)


def _observation_features(text: str) -> set[str]:
    normalized = _normalize_observation_reply(text)
    latin_terms = set(_OBSERVATION_LATIN_TERM_RE.findall(normalized))
    cjk_text = _OBSERVATION_LATIN_TERM_RE.sub("", normalized)
    cjk_bigrams = {cjk_text[i : i + 2] for i in range(len(cjk_text) - 1)}
    return latin_terms | cjk_bigrams


def _observation_feature_overlap(left_raw: str, right_raw: str) -> float:
    left = _observation_features(left_raw)
    right = _observation_features(right_raw)
    if not left or not right:
        return 0.0
    return 2 * len(left & right) / (len(left) + len(right))


def _is_similar_observation_reply(current: str, previous: str | None) -> bool:
    """Detect repeated companion observations even when phrased slightly differently."""
    if not current or not previous:
        return False

    pairs = [
        (current, previous),
        (_observation_core(current), _observation_core(previous)),
        (current, _observation_core(previous)),
        (_observation_core(current), previous),
    ]
    for left_raw, right_raw in pairs:
        left = _normalize_observation_reply(left_raw)
        right = _normalize_observation_reply(right_raw)
        if not left or not right:
            continue
        if left == right:
            return True
        shorter, longer = sorted((left, right), key=len)
        if len(shorter) >= 14 and shorter in longer:
            return True
        if min(len(left), len(right)) >= 12 and difflib.SequenceMatcher(None, left, right).ratio() >= 0.70:
            return True
        if _observation_feature_overlap(left_raw, right_raw) >= 0.27:
            return True
    return False


class MultiAgentOrchestrator:
    def __init__(self) -> None:
        self.memory = MemoryStore()
        self.skills = SkillLoader()
        self.context = ContextManager(self.memory, self.skills)
        self.router = RouterAgent()
        self.planner = PlannerAgent()
        self.mcp = MCPClient()
        self.registry = ToolRegistry(self.mcp)
        self.strategy = CompanionStrategy()
        self.agents = {
            "companion-agent": CompanionAgent(),
            "desktop-agent": DesktopAgent(),
            "terminal-agent": TerminalAgent(),
        }

        self._register_mcp_servers()

    def _register_mcp_servers(self) -> None:
        # Expose the local skills directory via the official MCP filesystem
        # server. On startup we try to discover its tools and surface them
        # through the regular ToolRegistry so the LLM can invoke them like
        # any other function.
        if settings.enable_filesystem_mcp:
            self.mcp.register_server(
                "filesystem",
                "npx",
                [
                    "-y",
                    "-p",
                    "ajv@8",
                    "-p",
                    "@modelcontextprotocol/server-filesystem",
                    "mcp-server-filesystem",
                    str(settings.skills_dir.resolve()),
                ],
            )

        if not settings.mcp_servers_json.strip():
            return
        try:
            raw_servers = json.loads(settings.mcp_servers_json)
        except json.JSONDecodeError:
            return
        if not isinstance(raw_servers, dict):
            return
        for name, config in raw_servers.items():
            if not isinstance(config, dict):
                continue
            command = config.get("command")
            args = config.get("args") or []
            if not command or not isinstance(args, list):
                continue
            self.mcp.register_server(str(name), str(command), [str(arg) for arg in args])

    async def bootstrap(self) -> None:
        """One-time async initialization (MCP discovery)."""
        try:
            count = await self.registry.load_mcp_tools(self.mcp)
            logger.info("MCP discovery loaded %d remote tool(s)", count)
        except Exception as exc:
            # Non-fatal: MCP discovery should never block the HTTP server.
            logger.warning("MCP discovery failed: %s", exc)

    def _build_artifacts(self, tool_calls: list[ToolCallRecord]) -> list[dict[str, str]]:
        artifacts: list[dict[str, str]] = []
        screenshots_root = settings.screenshots_dir.resolve()
        for tool_call in tool_calls:
            result = tool_call.result.strip()
            if tool_call.name != "screen.capture":
                continue
            try:
                path = Path(result).resolve()
            except OSError:
                continue
            if not path.exists():
                continue
            try:
                relative_path = path.relative_to(screenshots_root).as_posix()
            except ValueError:
                continue
            artifacts.append(
                {
                    "type": "screenshot",
                    "label": path.name,
                    "path": str(path),
                    "url": f"/artifacts/screenshots/{relative_path}",
                }
            )
        return artifacts

    def _build_task(self, request: ChatRequest, delegated: str, reply: str, tool_calls: list[ToolCallRecord]) -> dict:
        steps = []
        for index, tool_call in enumerate(tool_calls, start=1):
            status = infer_tool_status(tool_call.result, success=tool_call.success)
            steps.append(
                {
                    "id": f"step-{index}",
                    "title": tool_call.name,
                    "status": status,
                    "args": tool_call.args,
                    "detail": tool_call.result,
                }
            )
        if not steps:
            steps.append(
                {
                    "id": "step-1",
                    "title": "direct-response",
                    "status": "completed",
                    "detail": "The agent answered without tool use.",
                }
            )
        return {
            "title": request.message[:72],
            "owner": delegated,
            "status": self._overall_task_status(steps),
            "reply_preview": reply[:120],
            "step_count": len(steps),
            "steps": steps,
        }

    @staticmethod
    def _overall_task_status(steps: list[dict]) -> str:
        statuses = {step.get("status") for step in steps}
        if "failed" in statuses:
            return "failed"
        if "blocked" in statuses:
            return "blocked"
        if "running" in statuses:
            return "running"
        return "completed"

    async def _route(
        self, message: str, has_attachments: bool
    ) -> tuple[AgentTrace, IntentMatch]:
        """Classify intent, consulting the LLM router only when needed.

        Skips the LLM second-opinion (``router.plan``) when the local
        keyword classifier is *decisive* — i.e. it matched a near-unambiguous
        keyword — saving one model call per turn. Ambiguous or vague messages
        (multi-intent, no strong keyword) still get the LLM tiebreaker, which
        is exactly where it earns its cost.
        """
        local_intent = self.router.classify_local(message, has_attachments=has_attachments)
        if settings.router_skip_plan_when_decisive and local_intent.decisive:
            model_plan = None
        else:
            model_plan = await self.router.plan(message, self.registry.names(), local_intent)
        trace = self.planner.merge_intents(local_intent, model_plan)
        return trace, local_intent

    async def handle_chat(self, request: ChatRequest) -> ChatResponse:
        # Feed user message to the companion strategy for sentiment tracking
        # and future nudge tone decisions.
        self.strategy.record_message(request.session_id, request.message)
        self.memory.update_profile_from_message(request.session_id, request.message)

        trace, _local_intent = await self._route(
            request.message, has_attachments=bool(request.attachments),
        )
        delegated = trace.delegated_to or "companion-agent"
        prompt_context = self.context.build_for_agent(
            delegated,
            request.session_id,
            request.message,
            request.attachments,
        )
        self.memory.append(
            MemoryItem(session_id=request.session_id, role="user", content=request.message)
        )
        reply, tool_calls = await self.agents[delegated].handle(
            message=request.message,
            registry=self.registry,
            attachments=request.attachments,
            memory_summary=prompt_context,
            session_id=request.session_id,
        )

        enriched_reasoning = (
            f"{trace.reasoning} Skills available: {len(self.skills.list_skills())}. "
            f"MCP servers available: {len(self.mcp.list_servers())}."
        )
        final_trace = AgentTrace(
            active_agent=delegated,
            delegated_to=delegated,
            reasoning=enriched_reasoning,
            tool_calls=tool_calls,
        )
        self.memory.append(
            MemoryItem(session_id=request.session_id, role="assistant", content=reply)
        )
        artifacts = self._build_artifacts(tool_calls)
        task = self._build_task(request, delegated, reply, tool_calls)
        return ChatResponse(
            reply=reply,
            trace=final_trace,
            memory_summary=self.memory.summarize(request.session_id),
            task=task,
            artifacts=artifacts,
        )

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[dict]:
        """Run a chat turn and yield progress events for an SSE stream.

        Event order: ``start`` → ``intent`` → (``tool_start`` → ``tool_end``)*
        → ``tool_call`` (aggregated, one per tool, kept for back-compat)
        → ``delta`` (reply text in small chunks) → ``done``.

        ``tool_start`` / ``tool_end`` are emitted live as the agent steps
        through its tool calls, by wiring an async ``progress_cb`` into the
        agent. We run the agent as a background task and concurrently consume
        a queue the callback feeds, so events flush to the browser the moment
        each tool finishes — not at the end of the whole turn.
        """
        yield {"event": "start", "data": {"session_id": request.session_id, "message": request.message}}

        self.strategy.record_message(request.session_id, request.message)
        self.memory.update_profile_from_message(request.session_id, request.message)

        trace, local_intent = await self._route(
            request.message, has_attachments=bool(request.attachments),
        )
        delegated = trace.delegated_to or "companion-agent"
        prompt_context = self.context.build_for_agent(
            delegated,
            request.session_id,
            request.message,
            request.attachments,
        )
        self.memory.append(
            MemoryItem(session_id=request.session_id, role="user", content=request.message)
        )

        yield {
            "event": "intent",
            "data": {
                "delegated_to": delegated,
                "intent": local_intent.intent,
                "confidence": local_intent.confidence,
                "reasoning": trace.reasoning,
            },
        }

        # ---- Run the agent and stream live progress events --------------- #
        # The queue carries dicts shaped like {"event": ..., "data": ...} or
        # the sentinel ``None`` to signal the agent finished. Bounded size
        # keeps a runaway tool from buffering megabytes if the client is slow.
        progress_queue: asyncio.Queue = asyncio.Queue(maxsize=256)

        async def progress_cb(event: dict) -> None:
            await progress_queue.put(event)

        async def run_agent() -> tuple[str, list[ToolCallRecord]]:
            # Expose the streaming callback + session id to deeper code paths
            # (e.g. the permission broker called from inside a tool) via
            # ContextVars. Setting them here means they're inherited by every
            # async subtask the agent spawns, but stay isolated from concurrent
            # /api/chat/stream requests.
            progress_token = progress_cb_var.set(progress_cb)
            session_token = session_id_var.set(request.session_id)
            try:
                return await self.agents[delegated].handle(
                    message=request.message,
                    registry=self.registry,
                    attachments=request.attachments,
                    memory_summary=prompt_context,
                    session_id=request.session_id,
                    progress_cb=progress_cb,
                )
            finally:
                progress_cb_var.reset(progress_token)
                session_id_var.reset(session_token)
                # Always release the consumer loop, even if handle() raised.
                await progress_queue.put(None)

        agent_task: asyncio.Task = asyncio.create_task(run_agent())

        # Consume progress events as the agent produces them. The queue is
        # closed by a ``None`` sentinel pushed in the ``finally`` of run_agent.
        while True:
            event = await progress_queue.get()
            if event is None:
                break
            yield event

        # Agent task is now finished — surface its result or its exception.
        try:
            reply, tool_calls = await agent_task
        except Exception as exc:  # pragma: no cover — defensive net for the stream
            logger.exception("Streaming chat failed: %s", exc)
            yield {"event": "error", "data": {"message": f"agent execution failed: {exc}"}}
            return

        # Back-compat: emit aggregated ``tool_call`` events so older renderers
        # (and the existing test suite) keep working unchanged. New clients
        # should prefer the live ``tool_start`` / ``tool_end`` pair.
        for tc in tool_calls:
            yield {
                "event": "tool_call",
                "data": {
                    "name": tc.name,
                    "args": tc.args,
                    "result": tc.result,
                    "success": tc.success,
                    "step_id": tc.step_id,
                    "duration_ms": tc.duration_ms,
                },
            }

        # Chunk the reply for progressive rendering. Small enough chunks to feel
        # responsive, large enough that we don't spam the event loop on long
        # replies. ~30 chars per chunk plus a tiny await keeps Chrome's
        # EventSource happy.
        for chunk in _chunk_text(reply, 32):
            yield {"event": "delta", "data": {"text": chunk}}
            await asyncio.sleep(0)  # yield control so HTTP frames flush

        enriched_reasoning = (
            f"{trace.reasoning} Skills available: {len(self.skills.list_skills())}. "
            f"MCP servers available: {len(self.mcp.list_servers())}."
        )
        final_trace = AgentTrace(
            active_agent=delegated,
            delegated_to=delegated,
            reasoning=enriched_reasoning,
            tool_calls=tool_calls,
        )
        self.memory.append(
            MemoryItem(session_id=request.session_id, role="assistant", content=reply)
        )
        artifacts = self._build_artifacts(tool_calls)
        task = self._build_task(request, delegated, reply, tool_calls)

        yield {
            "event": "done",
            "data": {
                "reply": reply,
                "trace": final_trace.model_dump(),
                "memory_summary": self.memory.summarize(request.session_id),
                "task": task,
                "artifacts": artifacts,
            },
        }

    async def observe_screen(self, request: ObservationRequest) -> ObservationResponse:
        observation_state = self.memory.load_observation_state(request.session_id)

        # Active companion: if user is idle, skip the expensive screenshot entirely.
        if request.is_idle:
            observation_state.observation_count += 1
            self.memory.save_observation_state(observation_state)
            return ObservationResponse(
                reply="",
                trace=AgentTrace(
                    active_agent="desktop-agent",
                    delegated_to="desktop-agent",
                    reasoning="Idle mode — screen capture skipped.",
                    tool_calls=[],
                ),
                memory_summary=self.memory.summarize(request.session_id),
                task={"title": "idle-skip", "status": "skipped", "steps": []},
                artifacts=[],
                should_speak=False,
                significance="low",
            )

        # Ask the strategy engine whether we should proactively speak.
        nudge = self.strategy.decide_nudge(request)

        profile_context = self.context.build_for_agent(
            "desktop-agent",
            request.session_id,
            request.focus or "持续观察当前屏幕，像数字分身一样自然评论。",
            [],
        )
        desktop_agent = self.agents["desktop-agent"]
        reply, tool_calls, significance, should_speak, topic = await desktop_agent.observe_screen(
            message=request.focus or "请看看当前屏幕，如果有值得提醒或鼓励我的地方，就自然地说一句。",
            registry=self.registry,
            memory_summary=profile_context,
            observation_state=observation_state,
            trigger=request.trigger,
        )

        # If the strategy engine decided to nudge, prepend it.
        if nudge:
            reply = f"{nudge}\n\n{reply}" if reply else nudge
            should_speak = True

        deduped_reply = False
        if (
            request.trigger == "interval"
            and reply
            and _is_similar_observation_reply(reply, observation_state.last_comment)
        ):
            reply = ""
            significance = "low"
            should_speak = False
            deduped_reply = True

        observation_state.observation_count += 1
        if reply:
            observation_state.last_comment = reply
        observation_state.last_topic = topic
        self.memory.save_observation_state(observation_state)

        if reply:
            self.memory.append(
                MemoryItem(
                    session_id=request.session_id,
                    role="assistant",
                    content=f"[observation/{request.trigger}] {reply}",
                    tags=["observation", significance],
                )
            )

        trace = AgentTrace(
            active_agent="desktop-agent",
            delegated_to="desktop-agent",
            reasoning=(
                "Continuous screen observation for digital companion mode. "
                "Suppressed a highly similar observation reply."
                if deduped_reply
                else "Continuous screen observation for digital companion mode."
            ),
            tool_calls=tool_calls,
        )
        artifacts = self._build_artifacts(tool_calls)
        if deduped_reply:
            task = {
                "title": "similar-observation-skip",
                "owner": "desktop-agent",
                "status": "skipped",
                "reply_preview": "",
                "step_count": 1,
                "steps": [
                    {
                        "id": "step-1",
                        "title": "reply-deduplication",
                        "status": "skipped",
                        "detail": "Observation reply was too similar to the previous companion line.",
                    }
                ],
            }
        else:
            task = self._build_task(
                ChatRequest(
                    session_id=request.session_id,
                    message=request.focus or "continuous screen observation",
                ),
                "desktop-agent",
                reply,
                tool_calls,
            )
        return ObservationResponse(
            reply=reply,
            trace=trace,
            memory_summary=self.memory.summarize(request.session_id),
            task=task,
            artifacts=artifacts,
            should_speak=should_speak,
            significance=significance,
        )

    async def companion_chatter(self, request: ChatterRequest) -> ChatterResponse:
        """Proactively start a light topic (memory call-back / time note / news).

        Complements the screen-observation loop, which stays silent when the
        screen is unchanged: this keeps Shadow feeling present during quiet,
        steady work. Cadence is gated here so she doesn't natter; the desktop
        client additionally gates by real system-idle time so she stays quiet
        when the user actually steps away.
        """
        if not settings.enable_proactive_chatter:
            return ChatterResponse()
        if request.trigger == "interval" and not self.strategy.should_chatter(
            request.session_id, min_interval=settings.proactive_min_interval_seconds
        ):
            return ChatterResponse()

        source = self.strategy.next_chatter_source(
            request.session_id, ["memory", "time", "news"]
        )
        used_source = source
        if source == "time":
            reply = self._time_based_line(request)
        elif source == "news":
            reply = await self._news_based_line(request)
            if not reply:  # news unavailable → fall back to a memory call-back
                reply = await self._memory_based_line(request)
                used_source = "memory"
        else:  # memory
            reply = await self._memory_based_line(request)
            if not reply:  # model hiccup → fall back to a time note
                reply = self._time_based_line(request)
                used_source = "time"

        reply = self._clean_chatter(reply)
        # Reset the cadence clock even when we end up silent, so a failed turn
        # doesn't make us retry on every single tick.
        if not reply or self.strategy.is_recent_chatter(request.session_id, reply):
            self.strategy.record_chatter(request.session_id, reply)
            return ChatterResponse()

        self.strategy.record_chatter(request.session_id, reply)
        self.memory.append(
            MemoryItem(
                session_id=request.session_id,
                role="assistant",
                content=f"[chatter/{used_source}] {reply}",
                tags=["chatter", used_source],
            )
        )
        return ChatterResponse(reply=reply, should_speak=True, source=used_source)

    def _time_based_line(self, request: ChatterRequest) -> str:
        """A light, template-based note keyed to the time of day (no model call)."""
        import random

        hour = request.local_hour
        if hour is None:
            hour = time.localtime().tm_hour
        if 5 <= hour < 11:
            pool = ["早上好呀，今天想先从哪件事开始？", "新的一天开始啦，先喝口水再忙吧。"]
        elif 11 <= hour < 14:
            pool = ["到饭点啦，记得吃午饭哦。", "中午了，要不要起来活动下眼睛和肩膀？"]
        elif 14 <= hour < 18:
            pool = ["下午状态怎么样？需要我帮你理点什么吗？", "专注一会儿了，适当歇歇会更高效～"]
        elif 18 <= hour < 23:
            pool = ["晚上好，今天过得还顺利吗？", "夜里干活也别太拼，注意护眼。"]
        else:
            pool = ["这么晚还没休息呀？注意身体，别熬太久。", "夜深了，要不要定个收工的时间点？"]
        if request.work_minutes and request.work_minutes >= 50:
            pool.append(f"你已经连着忙了快 {request.work_minutes} 分钟了，起来动动吧。")
        return random.choice(pool)

    async def _memory_based_line(self, request: ChatterRequest) -> str:
        """A persona-voiced call-back to something from past conversations."""
        context = self.context.build_for_agent(
            "companion-agent",
            request.session_id,
            "主动找用户聊一句轻松的话",
            [],
        )
        instruction = (
            "现在主动、自然地跟用户说一句轻松的话，可以呼应你们之前聊过的内容、"
            "TA 关心的目标或最近的状态。只说一句（30 个汉字以内），口语、亲切，"
            "不要追问长问题，不要客套或重复套话，保持你的人设称呼和语气。"
            "直接给出这句话，不要任何解释或引号。"
        )
        return await self.agents["companion-agent"].compose_line(instruction, context)

    async def _news_based_line(self, request: ChatterRequest) -> str:
        """A persona-voiced, light mention of a free RSS headline (or "")."""
        headline = await news.random_headline()
        if not headline:
            return ""
        instruction = (
            "下面是一条新闻标题。像朋友随口一提那样，用一句轻松的话主动分享它，"
            "可以带一点你的看法或好奇，30 个汉字以内，保持你的人设语气，"
            "不要照抄标题、不要加引号或解释。\n"
            f"新闻标题：{headline.title}"
        )
        return await self.agents["companion-agent"].compose_line(instruction)

    @staticmethod
    def _clean_chatter(text: str) -> str:
        """Trim a proactive line to a single clean sentence."""
        text = (text or "").strip().strip("「」“”\"'")
        if not text:
            return ""
        first = text.splitlines()[0].strip()
        return first[:80]

    def get_profile(self, session_id: str) -> UserProfile:
        return self.memory.load_profile(session_id)

    def prepare_tts(self, request: VoiceTTSRequest) -> VoiceTTSResponse:
        return VoiceTTSResponse(
            text=request.text,
            voice=request.voice,
            rate=request.rate,
            pitch=request.pitch,
        )
