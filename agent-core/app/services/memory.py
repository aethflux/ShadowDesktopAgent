import json
import re
from pathlib import Path

from app.config import settings
from app.schemas import MemoryItem, ObservationState, TerminalSessionState, UserProfile
from app.services.vector_store import VectorStore


class MemoryStore:
    def __init__(self, root: Path | None = None, vector_store: VectorStore | None = None) -> None:
        self.root = root or settings.memory_dir
        self.root.mkdir(parents=True, exist_ok=True)
        # Semantic index is optional — falls back to no-op if chromadb missing.
        self.vector = vector_store or VectorStore()

    def _path(self, session_id: str) -> Path:
        return self.root / f"{session_id}.jsonl"

    def _terminal_path(self, session_id: str) -> Path:
        return self.root / f"terminal-{session_id}.json"

    def _profile_path(self, session_id: str) -> Path:
        return self.root / f"profile-{session_id}.json"

    def _observation_path(self, session_id: str) -> Path:
        return self.root / f"observation-{session_id}.json"

    def append(self, item: MemoryItem) -> None:
        with self._path(item.session_id).open("a", encoding="utf-8") as file:
            file.write(json.dumps(item.model_dump(), ensure_ascii=False) + "\n")
        # Mirror into the semantic index so future turns can retrieve by meaning,
        # not just recency. Observation spam is tagged; we skip low-signal ones.
        if "observation" not in (item.tags or []):
            self.vector.add(
                session_id=item.session_id,
                text=f"{item.role}: {item.content}",
                metadata={"role": item.role, "tags": ",".join(item.tags or [])},
            )

    def semantic_recall(self, session_id: str, query: str, top_k: int | None = None) -> list[str]:
        """Return text snippets most semantically similar to ``query``."""
        hits = self.vector.query(session_id, query, top_k=top_k)
        return [hit["text"] for hit in hits]

    def recent(self, session_id: str, limit: int = 12) -> list[MemoryItem]:
        path = self._path(session_id)
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
        return [MemoryItem.model_validate(json.loads(line)) for line in lines]

    def summarize(self, session_id: str) -> str:
        items = self.recent(session_id, limit=8)
        if not items:
            return "No prior memory."
        summary_parts = [f"{item.role}: {item.content[:80]}" for item in items]
        return " | ".join(summary_parts)

    def load_profile(self, session_id: str) -> UserProfile:
        path = self._profile_path(session_id)
        if not path.exists():
            return UserProfile(session_id=session_id)
        return UserProfile.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def save_profile(self, profile: UserProfile) -> None:
        self._profile_path(profile.session_id).write_text(
            json.dumps(profile.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_observation_state(self, session_id: str) -> ObservationState:
        path = self._observation_path(session_id)
        if not path.exists():
            return ObservationState(session_id=session_id)
        return ObservationState.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def save_observation_state(self, state: ObservationState) -> None:
        self._observation_path(state.session_id).write_text(
            json.dumps(state.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def update_profile_from_message(self, session_id: str, message: str) -> UserProfile:
        profile = self.load_profile(session_id)
        normalized = " ".join(message.split())
        if not normalized:
            return profile

        profile.interaction_count += 1
        profile.last_user_messages.append(normalized)
        profile.last_user_messages = profile.last_user_messages[-6:]

        self._extract_identity(profile, normalized)
        self._extract_scalar_field(
            profile,
            normalized,
            "role",
            [
                r"(?:\u6211\u662f|\u6211\u76ee\u524d\u662f)([^\uff0c\u3002\uff1b\n]{2,24})",
                r"(?:i am|i'm)\s+(?:a|an)?\s*([^,.!\n]{2,24})",
            ],
            blocked_prefixes=("\u60f3", "\u60f3\u627e", "\u6700\u8fd1", "\u6b63\u5728", "\u5728\u505a", "\u5e0c\u671b", "\u89c9\u5f97"),
        )
        self._extract_scalar_field(
            profile,
            normalized,
            "company",
            [
                r"(?:\u6211\u5728|\u76ee\u524d\u5728)([^\uff0c\u3002\uff1b\n]{2,30})(?:\u4e0a\u73ed|\u5de5\u4f5c|\u5b9e\u4e60)",
                r"(?:i work at|i'm at)\s+([^,.!\n]{2,40})",
            ],
        )
        self._extract_scalar_field(
            profile,
            normalized,
            "location",
            [
                r"(?:\u6211\u4f4f\u5728|\u957f\u671f\u5728)([^\uff0c\u3002\uff1b\n]{2,20})",
                r"(?:i live in)\s+([^,.!\n]{2,30})",
            ],
        )
        self._extract_scalar_field(
            profile,
            normalized,
            "bio",
            [
                r"(?:\u5173\u4e8e\u6211|\u7b80\u5355\u4ecb\u7ecd\u4e00\u4e0b\u6211\u81ea\u5df1[:\uff1a]?)([^\u3002\n]{6,80})",
                r"(?:about me[:\uff1a]?)([^.\n]{6,80})",
            ],
        )

        self._extract_list_items(
            profile.goals,
            normalized,
            [
                r"(?:\u6211\u60f3|\u6211\u5e0c\u671b|\u6211\u51c6\u5907|\u6211\u6253\u7b97)([^\uff0c\u3002\uff1b\n]{4,40})",
                r"(?:\u6211\u7684\u76ee\u6807\u662f)([^\uff0c\u3002\uff1b\n]{4,40})",
                r"(?:i want to|i hope to|my goal is to)\s+([^,.!\n]{4,50})",
            ],
        )
        self._extract_list_items(
            profile.preferences,
            normalized,
            [
                r"(?:\u6211\u559c\u6b22)([^\uff0c\u3002\uff1b\n]{2,30})",
                r"(?:\u6211\u66f4\u559c\u6b22)([^\uff0c\u3002\uff1b\n]{2,30})",
                r"(?:\u6211\u4e0d\u559c\u6b22)([^\uff0c\u3002\uff1b\n]{2,30})",
                r"(?:\u6211\u504f\u597d)([^\uff0c\u3002\uff1b\n]{2,30})",
                r"(?:i prefer|i like|i don't like)\s+([^,.!\n]{2,40})",
            ],
        )
        self._extract_list_items(
            profile.projects,
            normalized,
            [
                r"(?:\u6211\u5728\u505a|\u6211\u6b63\u5728\u505a|\u6700\u8fd1\u5728\u505a)([^\uff0c\u3002\uff1b\n]{4,40})",
                r"(?:\u8fd9\u4e2a\u9879\u76ee\u662f|\u6211\u7684\u9879\u76ee\u662f)([^\uff0c\u3002\uff1b\n]{4,40})",
                r"(?:i am working on|i'm building|my project is)\s+([^,.!\n]{4,50})",
            ],
        )
        self._extract_list_items(
            profile.relationship_notes,
            normalized,
            [
                r"(?:\u4f60\u53ef\u4ee5\u53eb\u6211)([^\uff0c\u3002\uff1b\n]{1,20})",
                r"(?:\u8ddf\u4f60\u8bf4\u4e00\u4e0b)([^\uff0c\u3002\uff1b\n]{4,40})",
                r"(?:remember that)\s+([^,.!\n]{4,50})",
            ],
        )
        self._extract_list_items(
            profile.notable_memories,
            normalized,
            [
                r"(?:\u6211\u6700\u8fd1)([^\u3002\uff1b\n]{4,50})",
                r"(?:\u4eca\u5929)([^\u3002\uff1b\n]{4,50})",
                r"(?:\u521a\u521a)([^\u3002\uff1b\n]{4,50})",
                r"(?:recently)\s+([^\.\n]{4,60})",
            ],
        )

        self.save_profile(profile)
        return profile

    def profile_summary(self, session_id: str) -> str:
        profile = self.load_profile(session_id)
        parts: list[str] = []
        if profile.preferred_name:
            parts.append(f"Preferred name: {profile.preferred_name}")
        elif profile.display_name:
            parts.append(f"Name: {profile.display_name}")
        if profile.role:
            parts.append(f"Role: {profile.role}")
        if profile.company:
            parts.append(f"Company: {profile.company}")
        if profile.location:
            parts.append(f"Location: {profile.location}")
        if profile.bio:
            parts.append(f"Bio: {profile.bio}")
        if profile.goals:
            parts.append(f"Goals: {', '.join(profile.goals[:4])}")
        if profile.preferences:
            parts.append(f"Preferences: {', '.join(profile.preferences[:4])}")
        if profile.projects:
            parts.append(f"Projects: {', '.join(profile.projects[:4])}")
        if profile.relationship_notes:
            parts.append(f"Relationship notes: {', '.join(profile.relationship_notes[:3])}")
        if profile.notable_memories:
            parts.append(f"Notable memories: {', '.join(profile.notable_memories[:3])}")
        if not parts:
            return "No long-term profile yet."
        return " | ".join(parts)

    def load_terminal_session(self, session_id: str) -> TerminalSessionState:
        path = self._terminal_path(session_id)
        if not path.exists():
            return TerminalSessionState(session_id=session_id, cwd=str(Path.cwd()))
        return TerminalSessionState.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def save_terminal_session(self, state: TerminalSessionState) -> None:
        self._terminal_path(state.session_id).write_text(
            json.dumps(state.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def reset_terminal_session(self, session_id: str) -> None:
        path = self._terminal_path(session_id)
        if path.exists():
            path.unlink()

    @staticmethod
    def _push_unique(target: list[str], value: str, limit: int) -> None:
        cleaned = value.strip(" \t\r\n,.;:\uff0c\u3002\uff1b\uff1a")
        if len(cleaned) < 2:
            return
        if cleaned not in target:
            target.append(cleaned)
        if len(target) > limit:
            del target[:-limit]

    def _extract_identity(self, profile: UserProfile, message: str) -> None:
        preferred_patterns = [
            r"(?:\u4f60\u53ef\u4ee5\u53eb\u6211|\u53eb\u6211)([^\uff0c\u3002\uff1b\n]{1,20})",
            r"(?:call me)\s+([^,.!\n]{1,20})",
        ]
        for pattern in preferred_patterns:
            matched = re.search(pattern, message, re.IGNORECASE)
            if matched:
                profile.preferred_name = matched.group(1).strip()
                return

        name_patterns = [
            r"(?:\u6211\u53eb)([^\uff0c\u3002\uff1b\n]{1,20})",
            r"(?:my name is)\s+([^,.!\n]{1,20})",
        ]
        for pattern in name_patterns:
            matched = re.search(pattern, message, re.IGNORECASE)
            if not matched:
                continue
            candidate = matched.group(1).strip()
            if candidate.startswith(("\u4e00\u4e2a", "\u4e00\u540d", "\u4e2a", "\u60f3", "\u6700\u8fd1", "\u6b63\u5728")):
                continue
            profile.display_name = candidate
            return

    def _extract_scalar_field(
        self,
        profile: UserProfile,
        message: str,
        field_name: str,
        patterns: list[str],
        blocked_prefixes: tuple[str, ...] = (),
    ) -> None:
        for pattern in patterns:
            matched = re.search(pattern, message, re.IGNORECASE)
            if not matched:
                continue
            value = matched.group(1).strip()
            if blocked_prefixes and value.startswith(blocked_prefixes):
                continue
            setattr(profile, field_name, value)
            return

    def _extract_list_items(self, target: list[str], message: str, patterns: list[str], limit: int = 8) -> None:
        for pattern in patterns:
            for match in re.finditer(pattern, message, re.IGNORECASE):
                self._push_unique(target, match.group(1), limit)
