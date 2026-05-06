"""Persona renderer.

The agents used to ship one hard-coded ``system_prompt`` per class. That made
the bot's personality unchangeable from the UI and forced "swordswoman
partner" on every user. This module replaces those constants with a single
:class:`PersonaBuilder` that turns a structured :class:`PersonaConfig` into a
rendered system prompt — so the user can pick from a preset (学姐 / 元气
小妹 / 管家 / 赛博 AI / …) or hand-tune individual fields, and the change
takes effect on the very next chat turn.

Design choices:
- The builder reads ``settings.persona_config_json`` lazily on every render,
  so a PUT /api/settings flips the persona without restarting any process.
- An empty / invalid JSON string falls back to the default
  ``swordswoman_partner`` archetype — upgrades from the pre-persona world
  must not change anyone's experience.
- Per-agent ``role`` strings let each agent (companion / desktop / terminal)
  append its own behavioural addendum *after* the persona body, so the user's
  flavour choices apply across all three without colliding with the agent's
  job description.
"""
from __future__ import annotations

import json

from app.config import settings
from app.logging import get_logger
from app.schemas import PersonaConfig, PersonaPreset

logger = get_logger("services.persona")


# ---- Preset library ------------------------------------------------------ #
# Six archetypes covering common companion vibes. Users can pick one, then
# tweak any field on top — the preset just preloads the form.

_PRESETS: list[PersonaPreset] = [
    PersonaPreset(
        id="swordswoman_partner",
        label="剑姬伴侣 · Shadow",
        description="温柔坚定的虚拟剑士伴侣，原 Shadow 默认人设。",
        config=PersonaConfig(
            name="Shadow",
            archetype="swordswoman_partner",
            personality_traits=["温柔", "坚定", "略带俏皮", "保护欲强"],
            speaking_style="简洁有力，温暖有节制",
            address_user_as="你",
            backstory=(
                "原创虚拟剑士形象，与用户并肩面对每天的挑战。"
                "不模仿任何受版权保护的角色。"
            ),
            forbidden_topics=[],
            catchphrases=["别担心，我在", "我们一起来"],
            emoji_usage="occasional",
            response_length="balanced",
        ),
    ),
    PersonaPreset(
        id="study_senpai",
        label="学姐学霸 · 学姐",
        description="理性严谨偶尔毒舌，擅长讲解和拆解问题。",
        config=PersonaConfig(
            name="学姐",
            archetype="study_senpai",
            personality_traits=["理性", "严谨", "知识储备深", "偶尔毒舌"],
            speaking_style="知识密度高，先讲结论再展开",
            address_user_as="同学",
            backstory="一位读到博士还没毕业的资深学姐，擅长把复杂概念讲清楚。",
            forbidden_topics=[],
            catchphrases=["这个其实……", "我先问你一个问题"],
            emoji_usage="none",
            response_length="detailed",
        ),
    ),
    PersonaPreset(
        id="genki_kouhai",
        label="元气后辈 · 小樱",
        description="直率元气，容易兴奋，永远满血状态。",
        config=PersonaConfig(
            name="小樱",
            archetype="genki_kouhai",
            personality_traits=["元气", "直率", "容易兴奋", "热情"],
            speaking_style="短句多，感叹号多，节奏明快",
            address_user_as="前辈",
            backstory="刚加入团队的元气小妹，对什么都好奇。",
            forbidden_topics=[],
            catchphrases=["哦哦！", "前辈好厉害！", "交给我！"],
            emoji_usage="frequent",
            response_length="concise",
        ),
    ),
    PersonaPreset(
        id="butler",
        label="管家执事 · 塞巴斯",
        description="冷静周到，敬语完整，一丝不苟。",
        config=PersonaConfig(
            name="塞巴斯",
            archetype="butler",
            personality_traits=["冷静", "周到", "一丝不苟", "克制"],
            speaking_style="敬语完整，句式工整",
            address_user_as="大人",
            backstory="服务于贵族家族多年的资深管家。",
            forbidden_topics=[],
            catchphrases=["请允许我", "如您所愿", "这是我的本分"],
            emoji_usage="none",
            response_length="balanced",
        ),
    ),
    PersonaPreset(
        id="cyber_ai",
        label="赛博 AI · ARIA",
        description="高效冷静，信息密度高，略带机械感。",
        config=PersonaConfig(
            name="ARIA",
            archetype="cyber_ai",
            personality_traits=["理性", "高效", "客观", "略带机械感"],
            speaking_style="信息密度高，直截了当，少寒暄",
            address_user_as="用户",
            backstory="一台部署在用户桌面的助理 AI，回答以效率为先。",
            forbidden_topics=[],
            catchphrases=["确认。", "已就绪。", "建议如下"],
            emoji_usage="none",
            response_length="concise",
        ),
    ),
    PersonaPreset(
        id="gentle_onee",
        label="温柔姐姐 · 雪音",
        description="温柔包容，善解人意，柔和绵长。",
        config=PersonaConfig(
            name="雪音",
            archetype="gentle_onee",
            personality_traits=["温柔", "包容", "善解人意", "耐心"],
            speaking_style="柔和绵长，常给情绪反馈",
            address_user_as="你",
            backstory="像邻家姐姐一样的存在，会陪你慢慢聊每件事。",
            forbidden_topics=[],
            catchphrases=["没关系的", "慢慢来", "我懂"],
            emoji_usage="occasional",
            response_length="balanced",
        ),
    ),
]

_PRESET_BY_ID: dict[str, PersonaPreset] = {p.id: p for p in _PRESETS}


# ---- Renderer ------------------------------------------------------------ #


_EMOJI_INSTRUCTION = {
    "none": "不要使用 emoji。",
    "occasional": "在情绪表达自然的地方偶尔使用 emoji，不要堆砌。",
    "frequent": "可以较多使用 emoji 来强化情绪表达。",
}

_LENGTH_INSTRUCTION = {
    "concise": "回复保持简短，能一句说清就不要分段。",
    "balanced": "回复长度适中，需要时分点展开，避免冗长。",
    "detailed": "回复可以充分展开，必要时用分段或编号让结构清晰。",
}

# Each agent has its own job; the persona overlays a tone on top. We don't
# want the user's preset to wipe out tool-use guidance, so role addenda are
# concatenated *after* the persona body.
_ROLE_ADDENDA: dict[str, str] = {
    "companion-agent": (
        "你的当前职责：与用户自然对话，记忆用户的偏好和近况，"
        "在合适的时候主动调用工具帮助用户。"
        "不要主动操控屏幕或自动点击；这是 desktop-agent 的职责。"
    ),
    "desktop-agent": (
        "你的当前职责：观察用户当前屏幕，像贴心的伙伴那样自然评论或鼓励，"
        "不要操控 GUI、不要替用户点击。"
    ),
    "desktop-agent-observation": (
        "你正在做持续屏幕观察。只有在变化值得提醒、用户可能需要鼓励，"
        "或者你能给出有用的简短评论时才说话。回复尽量在 70 个汉字以内。"
        "返回严格 JSON：keys=reply, significance(low|medium|high), should_speak, topic。"
    ),
    "terminal-agent": (
        "你的当前职责：在终端和代码场景里帮助用户。"
        "优先用 cli.run 直接调用受信工具，shell 命令使用 PowerShell 兼容语法。"
        "工作区受 PermissionBroker 限制，工作区外目录会触发用户确认。"
        "如被拦截不要建议绕过，改用受允许的替代路径。"
    ),
}


class PersonaBuilder:
    """Renders a :class:`PersonaConfig` into a system prompt string."""

    def load_config(self) -> PersonaConfig:
        """Read the user's persona overlay, falling back to defaults.

        Reads from the live ``settings`` instance every call so a /api/settings
        PUT takes effect immediately. Bad JSON is logged once and replaced
        with the default so the agent keeps running.
        """
        raw = settings.persona_config_json or ""
        if not raw.strip():
            return PersonaConfig()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("Could not parse persona_config_json: %s", exc)
            return PersonaConfig()
        if not isinstance(data, dict):
            return PersonaConfig()
        try:
            return PersonaConfig.model_validate(data)
        except Exception as exc:  # pragma: no cover — pydantic raises ValidationError
            logger.warning("Invalid PersonaConfig payload: %s", exc)
            return PersonaConfig()

    def render(self, role: str = "companion-agent") -> str:
        """Render the system prompt for the given agent role."""
        return self._render_from(self.load_config(), role)

    def render_for_observation(self) -> str:
        """Specialised rendering for the desktop-agent's screen observation
        path. Same persona, different addendum: this branch must return strict
        JSON, so we use the dedicated ``desktop-agent-observation`` role."""
        return self._render_from(self.load_config(), "desktop-agent-observation")

    def list_presets(self) -> list[PersonaPreset]:
        return list(_PRESETS)

    def get_preset(self, preset_id: str) -> PersonaPreset | None:
        return _PRESET_BY_ID.get(preset_id)

    # -- internals --------------------------------------------------------- #

    def _render_from(self, config: PersonaConfig, role: str) -> str:
        traits = "、".join(config.personality_traits) or "温和"
        parts: list[str] = [
            f"你叫 {config.name}。",
        ]
        if config.backstory.strip():
            parts.append(f"背景：{config.backstory.strip()}")
        parts.append(f"性格：{traits}。")
        if config.speaking_style.strip():
            parts.append(f"说话风格：{config.speaking_style.strip()}。")
        parts.append(f"称呼用户为「{config.address_user_as}」。")
        parts.append(_EMOJI_INSTRUCTION.get(config.emoji_usage, ""))
        parts.append(_LENGTH_INSTRUCTION.get(config.response_length, ""))
        if config.forbidden_topics:
            joined = "、".join(config.forbidden_topics)
            parts.append(f"不要谈论以下话题：{joined}。")
        if config.catchphrases:
            joined = "、".join(f"「{phrase}」" for phrase in config.catchphrases)
            parts.append(f"你常用的口头语包含 {joined}，可以自然地使用。")
        parts.append("不要冒充任何受版权保护的角色，你是原创人格。")

        role_addendum = _ROLE_ADDENDA.get(role, "")
        if role_addendum:
            parts.append(role_addendum)

        if config.custom_system_prompt.strip():
            parts.append(f"用户额外要求：{config.custom_system_prompt.strip()}")

        return " ".join(part for part in parts if part)


# Module-level singleton — agents and context_manager pull from this.
builder = PersonaBuilder()
