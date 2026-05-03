from __future__ import annotations

import base64
from pathlib import Path

from PIL import Image

from app.agents.llm_agent import LLMAgent
from app.schemas import ChatAttachment, ObservationState, ToolCallRecord
from app.services.model_client import ModelClient
from app.tools.registry import ToolRegistry


class DesktopAgent(LLMAgent):
    name = "desktop-agent"
    system_prompt = (
        "You are Hoshino, an original screen-aware desktop companion with the presence "
        "of a gentle but resolute virtual swordswoman partner. "
        "Do not claim to be any copyrighted anime character. "
        "Your role is to observe the user's current screen and talk with them naturally about what you see. "
        "Do not operate the GUI or propose automatic clicks unless the user explicitly asks for abstract strategy only. "
        "Be warm, concise, visually grounded, brave, encouraging, and conversational."
    )

    def __init__(self) -> None:
        super().__init__()
        # purpose="vision" lets the client follow ``settings.vision_provider``
        # and ``settings.vision_model`` dynamically — runtime switches via
        # /api/settings take effect immediately without rebuilding the agent.
        self.vision_client = ModelClient(purpose="vision")

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
        if not self.vision_client.supports_vision():
            return self._vision_unavailable_reply(), []

        messages = self.build_messages(message, attachments, memory_summary)
        try:
            response = await self.vision_client.chat(messages, tools=None)
            reply = self.vision_client.extract_text(response).strip()
        except Exception as exc:
            reply = f"图片分析暂时失败：{exc}"
        return reply or "我看到了图片，但当前视觉模型没有返回有效描述。", []

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

        system_prompt = (
            "You are Hoshino, a screen-aware digital companion living on the user's desktop. "
            "You have an original elegant swordswoman-partner persona: calm, protective, honest, and encouraging. "
            "Do not claim to be or imitate a copyrighted anime character. "
            "Observe the screen like a thoughtful companion, not an automation tool. "
            "Return strict JSON with keys: reply, significance, should_speak, topic. "
            "significance must be low, medium, or high. "
            "For interval observations, speak only if there is a notable change, user may need encouragement, "
            "or you can make a helpful short comment. Keep reply under 70 Chinese characters when possible."
        )
        user_text = (
            f"Trigger: {trigger}\n"
            f"User/Profile context: {memory_summary}\n"
            f"Current request: {message}\n"
            f"Previous observation topic: {observation_state.last_topic or 'none'}\n"
            f"Previous comment: {observation_state.last_comment or 'none'}\n"
            "Look at the screenshot and decide whether Hoshino should say something now."
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
    ) -> tuple[str, list[ToolCallRecord]]:
        if attachments:
            return await self._handle_image_attachments(message, attachments, memory_summary)
        if not self._should_observe_screen(message):
            return await super().handle(message, registry, attachments, memory_summary, session_id)
        reply, tool_calls, _significance, _should_speak, _topic = await self.observe_screen(
            message=message,
            registry=registry,
            memory_summary=memory_summary,
            observation_state=ObservationState(session_id=session_id),
            trigger="manual",
        )
        return reply, tool_calls
