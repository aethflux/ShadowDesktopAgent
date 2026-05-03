from __future__ import annotations

import asyncio
import json
from pathlib import Path
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
    MemoryItem,
    ObservationRequest,
    ObservationResponse,
    ToolCallRecord,
    UserProfile,
    VoiceTTSRequest,
    VoiceTTSResponse,
)
from app.services.companion_strategy import CompanionStrategy
from app.services.context_manager import ContextManager
from app.services.mcp_client import MCPClient
from app.services.memory import MemoryStore
from app.services.skill_loader import SkillLoader
from app.tools.registry import ToolRegistry

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
            # Combine the explicit ``success`` flag (set by the agent when the
            # tool raised or wasn't registered) with string heuristics on the
            # tool's stdout — terminal commands can exit non-zero without
            # raising, so the heuristic catches that case.
            lowered_result = tool_call.result.lower()
            heuristic_failed = (
                "failed:" in lowered_result
                or "blocked unsafe command" in lowered_result
                or ("exit=" in lowered_result and "exit=0" not in lowered_result)
            )
            failed = (not tool_call.success) or heuristic_failed
            steps.append(
                {
                    "id": f"step-{index}",
                    "title": tool_call.name,
                    "status": "failed" if failed else "completed",
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
            "status": "failed" if any(step["status"] == "failed" for step in steps) else "completed",
            "reply_preview": reply[:120],
            "step_count": len(steps),
            "steps": steps,
        }

    async def handle_chat(self, request: ChatRequest) -> ChatResponse:
        # Feed user message to the companion strategy for sentiment tracking
        # and future nudge tone decisions.
        self.strategy.record_message(request.session_id, request.message)
        self.memory.append(
            MemoryItem(session_id=request.session_id, role="user", content=request.message)
        )
        self.memory.update_profile_from_message(request.session_id, request.message)
        prompt_context = self.context.build_prompt_context(
            request.session_id,
            request.message,
            request.attachments,
        )

        local_intent = self.router.classify_local(
            request.message,
            has_attachments=bool(request.attachments),
        )
        model_plan = await self.router.plan(request.message, self.registry.names(), local_intent)
        trace = self.planner.merge_intents(local_intent, model_plan)
        delegated = trace.delegated_to or "companion-agent"
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

        Event order: ``start`` → ``intent`` → ``tool_call`` (one per tool) →
        ``delta`` (reply text in small chunks) → ``done``.

        Tool calls are emitted *after* the agent finishes (the underlying
        agent.handle is not yet streaming-aware), but the chunked delta makes
        the UX feel responsive immediately.
        """
        yield {"event": "start", "data": {"session_id": request.session_id, "message": request.message}}

        self.strategy.record_message(request.session_id, request.message)
        self.memory.append(
            MemoryItem(session_id=request.session_id, role="user", content=request.message)
        )
        self.memory.update_profile_from_message(request.session_id, request.message)
        prompt_context = self.context.build_prompt_context(
            request.session_id, request.message, request.attachments,
        )

        local_intent = self.router.classify_local(
            request.message, has_attachments=bool(request.attachments),
        )
        model_plan = await self.router.plan(request.message, self.registry.names(), local_intent)
        trace = self.planner.merge_intents(local_intent, model_plan)
        delegated = trace.delegated_to or "companion-agent"

        yield {
            "event": "intent",
            "data": {
                "delegated_to": delegated,
                "intent": local_intent.intent,
                "confidence": local_intent.confidence,
                "reasoning": trace.reasoning,
            },
        }

        try:
            reply, tool_calls = await self.agents[delegated].handle(
                message=request.message,
                registry=self.registry,
                attachments=request.attachments,
                memory_summary=prompt_context,
                session_id=request.session_id,
            )
        except Exception as exc:  # pragma: no cover — defensive net for the stream
            logger.exception("Streaming chat failed: %s", exc)
            yield {"event": "error", "data": {"message": f"agent execution failed: {exc}"}}
            return

        for tc in tool_calls:
            yield {
                "event": "tool_call",
                "data": {"name": tc.name, "args": tc.args, "result": tc.result, "success": tc.success},
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

        profile_context = self.context.build_prompt_context(
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
            reply = f"{nudge}\n\n{reply}"
            should_speak = True
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
            reasoning="Continuous screen observation for digital companion mode.",
            tool_calls=tool_calls,
        )
        artifacts = self._build_artifacts(tool_calls)
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

    def get_profile(self, session_id: str) -> UserProfile:
        return self.memory.load_profile(session_id)

    def prepare_tts(self, request: VoiceTTSRequest) -> VoiceTTSResponse:
        return VoiceTTSResponse(
            text=request.text,
            voice=request.voice,
            rate=request.rate,
            pitch=request.pitch,
        )
