from __future__ import annotations

import base64
from pathlib import Path

from PIL import Image

from app.agents.llm_agent import LLMAgent, ProgressCallback
from app.schemas import ChatAttachment, ObservationState, ToolCallRecord
from app.services.persona import builder as persona_builder
from app.tools.registry import ToolRegistry


class DesktopAgent(LLMAgent):
    """Screen-aware companion. Persona body and role guidance now flow
    through :class:`PersonaBuilder`; this class only owns the observation
    pipeline (screen capture, hashing, structured-JSON observation).

    The vision client, ``_handle_image_attachments`` and
    ``_vision_unavailable_reply`` are inherited from :class:`LLMAgent` — the
    base class now owns image handling so every agent degrades gracefully on
    image input."""

    name = "desktop-agent"
    allowed_tool_names = frozenset({"screen.capture"})

    def _should_observe_screen(self, message: str) -> bool:
        lowered = message.lower()
        keywords = [
            "屏幕", "界面", "窗口", "画面", "看一下", "看看", "你看到了什么",
            "screen", "window", "ui", "what do you see", "look at"
        ]
        return any(keyword in lowered for keyword in keywords)

    @staticmethod
    def _to_data_url(path: str) -> str:
        file_path = Path(path)
        encoded = base64.b64encode(file_path.read_bytes()).decode("utf-8")
        return f"data:image/png;base64,{encoded}"

    @staticmethod
    def _screen_hash(path: str) -> str:
        with Image.open(path) as image:
            grayscale = image.convert("L").resize((8, 8), Image.Resampling.LANCZOS)
            pixels = list(grayscale.getdata())
        average = sum(pixels) / len(pixels)
        bits = "".join("1" if pixel >= average else "0" for pixel in pixels)
        return f"{int(bits, 2):016x}"

    @staticmethod
    def _hash_distance(left: str | None, right: str | None) -> int:
        if not left or not right:
            return 64
        return (int(left, 16) ^ int(right, 16)).bit_count()

    async def observe_screen(
        self,
        message: str,
        registry: ToolRegistry,
        memory_summary: str,
        observation_state: ObservationState,
        trigger: str,
    ) -> tuple[str, list[ToolCallRecord], str, bool, str | None]:
        tool_calls: list[ToolCallRecord] = []
        if not self.vision_client.supports_vision():
            reply = self._vision_unavailable_reply()
            return reply, tool_calls, "low", trigger == "manual", "vision-unavailable"

        screenshot_path = await registry.arun("screen.capture", {})
        tool_calls.append(ToolCallRecord(name="screen.capture", args={}, result=screenshot_path))
        screen_hash = self._screen_hash(screenshot_path)

        if (
            trigger == "interval"
            and observation_state.last_screen_hash
            and self._hash_distance(observation_state.last_screen_hash, screen_hash) <= 4
        ):
            observation_state.last_screen_hash = screen_hash
            return "", tool_calls, "low", False, observation_state.last_topic or "screen-unchanged"

        observation_state.last_screen_hash = screen_hash
        screenshot_url = self._to_data_url(screenshot_path)

        # Compose the observation prompt from the same PersonaBuilder so the
        # user's tone choices apply here too. The dedicated
        # ``desktop-agent-observation`` role addendum supplies the strict-JSON
        # output requirement and length cap.
        system_prompt = persona_builder.render_for_observation()
        user_text = (
            f"Trigger: {trigger}\n"
            f"User/Profile context: {memory_summary}\n"
            f"Current request: {message}\n"
            f"Previous observation topic: {observation_state.last_topic or 'none'}\n"
            f"Previous comment: {observation_state.last_comment or 'none'}\n"
            "判断规则：\n"
            "- 如果这是定时观察、屏幕仍是同一应用/同一任务，或你的评论基本只是在重复上一条，"
            "就返回 reply=\"\"、significance=\"low\"、should_speak=false。\n"
            "- 不要只为了说点什么而把上一条换个说法重复。\n"
            "- 如果确实要说，就点出一个新出现的可见细节或一个有用的下一步，"
            "并保持你设定人格的措辞。\n"
            "- 没有新信息时，避免「我看了一下屏幕」这类空话。\n"
            "看一下截图，判断 Shadow 现在是否该说点什么。"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": screenshot_url}},
                ],
            },
        ]

        try:
            result = await self.vision_client.complete_structured_messages(messages)
            reply = str(result.get("reply") or "").strip()
            significance = str(result.get("significance") or "medium").strip().lower()
            should_speak = bool(result.get("should_speak", True))
            topic = result.get("topic")
        except ValueError as exc:
            reply = f"视觉分析当前不可用：{exc}"
            significance = "low"
            should_speak = trigger == "manual"
            topic = "vision-unavailable"
        except Exception as exc:
            reply = f"我看了一眼屏幕，但视觉分析暂时失败：{exc}"
            significance = "low"
            should_speak = trigger == "manual"
            topic = "vision-error"

        if significance not in {"low", "medium", "high"}:
            significance = "medium"
        if not reply:
            if trigger == "interval":
                return "", tool_calls, "low", False, str(topic) if topic else None
            reply = "我看了一下屏幕，暂时没有特别需要提醒你的变化。"
            significance = "low"
            should_speak = trigger == "manual"

        return reply, tool_calls, significance, should_speak, str(topic) if topic else None

    async def handle(
        self,
        message: str,
        registry: ToolRegistry,
        attachments: list[ChatAttachment],
        memory_summary: str,
        session_id: str,
        progress_cb: ProgressCallback | None = None,
    ) -> tuple[str, list[ToolCallRecord]]:
        if attachments:
            return await self._handle_image_attachments(message, attachments, memory_summary)
        if not self._should_observe_screen(message):
            return await super().handle(
                message, registry, attachments, memory_summary, session_id,
                progress_cb=progress_cb,
            )
        reply, tool_calls, _significance, _should_speak, _topic = await self.observe_screen(
            message=message,
            registry=registry,
            memory_summary=memory_summary,
            observation_state=ObservationState(session_id=session_id),
            trigger="manual",
        )
        return reply, tool_calls
