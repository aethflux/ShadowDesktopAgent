from __future__ import annotations

from dataclasses import dataclass

from app.schemas import IntentMatch
from app.services.model_client import ModelClient


@dataclass(frozen=True)
class IntentRule:
    intent: str
    delegated_to: str
    keywords: tuple[str, ...]
    tools: tuple[str, ...] = ()


class RouterAgent:
    def __init__(self) -> None:
        self.model_client = ModelClient()
        self.rules = (
            IntentRule(
                intent="screen_observation",
                delegated_to="desktop-agent",
                keywords=(
                    "屏幕", "截图", "截个图", "截个屏", "截屏", "屏幕观察",
                    "观察", "看屏幕", "看看屏幕", "当前界面",
                    "当前窗口", "我的窗口", "我当前的窗口", "画面",
                    "你看到了什么", "帮我看看", "screen", "screenshot", "what do you see",
                    "look at my screen", "window", "user interface",
                ),
                tools=("screen.capture",),
            ),
            IntentRule(
                intent="continuous_companion",
                delegated_to="desktop-agent",
                keywords=(
                    "持续观察", "持续陪伴", "主动评论", "主动提醒", "一直看", "陪着我",
                    "数字分身", "常驻", "watch continuously", "companion mode",
                ),
                tools=("screen.capture",),
            ),
            IntentRule(
                intent="terminal",
                delegated_to="terminal-agent",
                keywords=(
                    "命令行", "终端", "shell", "powershell", "cmd", "terminal", "运行命令",
                    "执行命令", "npm", "pip", "git", "pytest", "uvicorn", "ls", "dir", "cd ",
                ),
                tools=("terminal.run", "terminal.reset"),
            ),
            IntentRule(
                intent="coding",
                delegated_to="terminal-agent",
                keywords=(
                    "代码", "实现", "修复", "优化", "改进", "增强", "升级", "提升",
                    "意图", "路由", "匹配", "分类", "bug", "报错", "重构", "测试", "文件", "函数",
                    "class", "function", "typescript", "python", "build", "compile",
                    "implement", "fix", "refactor", "test",
                ),
                tools=("terminal.run",),
            ),
            IntentRule(
                intent="voice",
                delegated_to="companion-agent",
                keywords=(
                    "语音", "朗读", "播报", "声音", "麦克风", "asr", "tts", "voice",
                    "speech", "speak", "read aloud", "read this aloud", "aloud",
                ),
            ),
            IntentRule(
                intent="memory_profile",
                delegated_to="companion-agent",
                keywords=(
                    "记住", "别忘了", "我叫", "叫我", "我喜欢", "我不喜欢", "我的目标",
                    "我是", "我在做", "最近在做", "remember", "call me", "my goal",
                ),
            ),
            IntentRule(
                intent="persona",
                delegated_to="companion-agent",
                keywords=(
                    "人设", "人格", "性格", "形象", "桌宠形象", "数字人", "角色",
                    "亚丝娜", "剑士", "persona", "character", "avatar",
                ),
            ),
        )

    def classify_local(self, message: str, has_attachments: bool = False) -> IntentMatch:
        lowered = message.lower()
        scores: dict[str, float] = {}
        signals: dict[str, list[str]] = {}
        delegates: dict[str, str] = {}
        tools: dict[str, list[str]] = {}

        for rule in self.rules:
            for keyword in rule.keywords:
                if keyword.lower() in lowered:
                    scores[rule.intent] = scores.get(rule.intent, 0.0) + self._keyword_weight(keyword)
                    signals.setdefault(rule.intent, []).append(keyword)
                    delegates[rule.intent] = rule.delegated_to
                    tools.setdefault(rule.intent, []).extend(rule.tools)

        if has_attachments:
            scores["screen_observation"] = scores.get("screen_observation", 0.0) + 1.2
            signals.setdefault("screen_observation", []).append("image-attachment")
            delegates["screen_observation"] = "desktop-agent"

        if not scores:
            return IntentMatch(
                intent="conversation",
                delegated_to="companion-agent",
                confidence=0.45,
                reasoning="No strong operational intent matched; treating as companion conversation.",
                signals=[],
            )

        intent, score = max(scores.items(), key=lambda item: item[1])
        confidence = min(0.95, 0.38 + score * 0.16)
        matched_signals = signals.get(intent, [])
        unique_tools = list(dict.fromkeys(tools.get(intent, [])))
        return IntentMatch(
            intent=intent,
            delegated_to=delegates.get(intent, "companion-agent"),
            confidence=confidence,
            reasoning=(
                f"Local intent matched `{intent}` via signals: "
                f"{', '.join(matched_signals[:6]) or 'none'}."
            ),
            signals=matched_signals[:8],
            tool_candidates=unique_tools,
        )

    async def plan(
        self,
        message: str,
        tool_names: list[str],
        local_intent: IntentMatch,
    ) -> dict | None:
        system_prompt = (
            "You are an intent classifier for a desktop digital companion multi-agent system. "
            "Return strict JSON with keys: intent, delegated_to, confidence, reasoning, tool_candidates. "
            "intent must be one of conversation, screen_observation, continuous_companion, terminal, "
            "coding, memory_profile, persona, voice, unknown. "
            "delegated_to must be one of companion-agent, desktop-agent, terminal-agent. "
            "Prefer desktop-agent only for screen observation or continuous companion screen watching. "
            "Prefer terminal-agent for code execution, shell commands, build/test/debug tasks. "
            "Prefer companion-agent for persona, voice, memory/profile, and normal conversation."
        )
        user_prompt = (
            f"User message: {message}\n"
            f"Available tools: {tool_names}\n"
            f"Local classifier result: {local_intent.model_dump()}\n"
            "Classify the user's intent. If local and model disagree, explain why."
        )
        try:
            return await self.model_client.complete_structured(system_prompt, user_prompt)
        except Exception:
            return None

    @staticmethod
    def _keyword_weight(keyword: str) -> float:
        if len(keyword) >= 6:
            return 1.6
        if len(keyword) >= 3:
            return 1.2
        return 0.9
