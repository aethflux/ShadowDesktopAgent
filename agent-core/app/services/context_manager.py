from app.schemas import ChatAttachment
from app.services.memory import MemoryStore
from app.services.skill_loader import SkillLoader


_AGENT_CONTEXT_POLICY: dict[str, dict[str, object]] = {
    "companion-agent": {
        "recent_limit": 8,
        "semantic_top_k": 4,
        "include_profile": True,
        "include_skills": True,
        "guidance": (
            "结合画像和记忆保持连贯，但除非对当前回复有帮助，否则不要重复旧细节。"
        ),
    },
    "terminal-agent": {
        "recent_limit": 4,
        "semantic_top_k": 3,
        "include_profile": True,
        "include_skills": True,
        "guidance": (
            "Prioritize project, command, file, error and tool-related context. Ignore unrelated "
            "small talk unless it changes the user's constraints."
        ),
    },
    "desktop-agent": {
        "recent_limit": 3,
        "semantic_top_k": 2,
        "include_profile": True,
        "include_skills": False,
        "guidance": (
            "优先关注当前可见的屏幕状态、上一个观察主题和用户偏好。"
            "评论保持简短，避免重复之前的观察。"
        ),
    },
}


class ContextManager:
    def __init__(self, memory_store: MemoryStore, skill_loader: SkillLoader) -> None:
        self.memory_store = memory_store
        self.skill_loader = skill_loader

    def build_prompt_context(
        self,
        session_id: str,
        user_message: str,
        attachments: list[ChatAttachment],
    ) -> str:
        return self.build_for_agent(
            "companion-agent",
            session_id,
            user_message,
            attachments,
        )

    def build_for_router(
        self,
        user_message: str,
        attachments: list[ChatAttachment],
        tool_names: list[str],
        session_id: str | None = None,
    ) -> str:
        """Small, stable routing context.

        The router should not see the full memory pack: past memories can bias
        intent classification and they also waste tokens. What it *does* need
        is dialogue state — the last few raw turns — so a signal-free
        follow-up like "再跑一次" can be disambiguated. Long-term memory
        (semantic recall, profile) stays excluded.
        """
        attachment_summary = ", ".join(
            attachment.path or attachment.mime_type or "inline-image"
            for attachment in attachments
        ) or "none"
        parts = [
            f"User message: {user_message}",
            f"Attachments: {attachment_summary}",
            f"Available tools: {', '.join(tool_names)}",
        ]
        if session_id:
            dialogue = self._recent_dialogue(session_id)
            if dialogue:
                parts.append(f"Recent dialogue (oldest first):\n{dialogue}")
        return "\n".join(parts)

    def _recent_dialogue(self, session_id: str, max_items: int = 6) -> str:
        """Last few plain chat turns; observation/chatter noise is excluded."""
        items = self.memory_store.recent(session_id, limit=max_items * 2)
        lines = [
            f"{item.role}: {item.content[:80]}"
            for item in items
            if not {"observation", "chatter"} & set(item.tags or [])
        ]
        return "\n".join(lines[-max_items:])

    def build_for_agent(
        self,
        agent_name: str,
        session_id: str,
        user_message: str,
        attachments: list[ChatAttachment],
    ) -> str:
        policy = _AGENT_CONTEXT_POLICY.get(agent_name, _AGENT_CONTEXT_POLICY["companion-agent"])
        recent_limit = int(policy["recent_limit"])
        semantic_top_k = int(policy["semantic_top_k"])

        memory_summary = self.memory_store.summarize(session_id, limit=recent_limit)
        profile_summary = (
            self.memory_store.profile_summary(session_id)
            if bool(policy["include_profile"])
            else "not included for this agent."
        )
        attachment_summary = ", ".join(
            attachment.path or attachment.mime_type or "inline-image"
            for attachment in attachments
        ) or "none"

        # Semantic recall: fetch memories related by *meaning* to the current
        # message, not just the most recent N turns. This fills the gap that
        # a pure JSONL tail buffer cannot — e.g. the user mentioned a project
        # name three days ago and brings it up again now.
        semantic_hits = self.memory_store.semantic_recall(
            session_id,
            user_message,
            top_k=semantic_top_k,
        )
        semantic_block = (
            " | ".join(hit[:100] for hit in semantic_hits) if semantic_hits else "none"
        )

        # Skill injection: match the user's message against skill triggers
        # and prepend the matched skill prompts so the agent can draw on
        # domain-specific guidance without needing a separate tool call.
        matched_skills = []
        if bool(policy["include_skills"]):
            self.skill_loader.reload()
            matched_skills = self.skill_loader.match(user_message)
        skill_block = (
            "\n\n".join(f"[Skill: {s.name}]\n{s.prompt}" for s in matched_skills)
            if matched_skills else ""
        )

        # Persona description is no longer hard-coded here. The system prompt
        # is fully owned by ``PersonaBuilder`` (see ``services/persona.py``)
        # and injected by the agent's ``get_system_prompt``. This block is
        # now pure runtime context — memory, attachments, and active skills.
        parts = [
            f"Context policy for {agent_name}: {policy['guidance']} ",
            f"Long-term user profile: {profile_summary}. ",
            f"Recent memory: {memory_summary}. ",
            f"Semantically related memories: {semantic_block}. ",
            f"Attachments: {attachment_summary}. ",
        ]
        if skill_block:
            parts.append(f"\n\nActive skills:\n{skill_block}\n")
        parts.append(f"User request: {user_message}")
        return "".join(parts)
