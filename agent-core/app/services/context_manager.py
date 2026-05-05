from app.schemas import ChatAttachment
from app.services.memory import MemoryStore
from app.services.skill_loader import SkillLoader


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
        memory_summary = self.memory_store.summarize(session_id)
        profile_summary = self.memory_store.profile_summary(session_id)
        attachment_summary = ", ".join(
            attachment.path or attachment.mime_type or "inline-image"
            for attachment in attachments
        ) or "none"

        # Semantic recall: fetch memories related by *meaning* to the current
        # message, not just the most recent N turns. This fills the gap that
        # a pure JSONL tail buffer cannot — e.g. the user mentioned a project
        # name three days ago and brings it up again now.
        semantic_hits = self.memory_store.semantic_recall(session_id, user_message)
        semantic_block = (
            " | ".join(hit[:100] for hit in semantic_hits) if semantic_hits else "none"
        )

        # Skill injection: match the user's message against skill triggers
        # and prepend the matched skill prompts so the agent can draw on
        # domain-specific guidance without needing a separate tool call.
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
            f"Long-term user profile: {profile_summary}. ",
            f"Recent memory: {memory_summary}. ",
            f"Semantically related memories: {semantic_block}. ",
            f"Attachments: {attachment_summary}. ",
        ]
        if skill_block:
            parts.append(f"\n\nActive skills:\n{skill_block}\n")
        parts.append(f"User request: {user_message}")
        return "".join(parts)
