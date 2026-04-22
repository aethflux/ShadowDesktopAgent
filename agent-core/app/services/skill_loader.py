"""Skill discovery and loading with YAML frontmatter support.

Skill directory layout (one skill per directory)::

    skills/
      general-assistant/
        skill.md       ← YAML frontmatter + prompt content
        script.py       ← optional executable extension (future)
      github-review/
        skill.md
      english-tutor/
        skill.md

``skill.md`` format::

    ---
    name: general-assistant
    description: A concise operational assistant for coding and workflow questions.
    triggers:
      - code
      - plan
      - explain
      - summarize
    ---
    # System Prompt

    You are a precise, action-oriented helper. When asked about files,
    terminal work, or desktop actions, propose tool use or delegation.
    Prefer short, concrete answers with numbered steps when applicable.

The loader also maintains a ``_trigger_index`` mapping every trigger keyword
to the list of skills that contain it, so that ``match(message)`` runs in
O(trigger_words) rather than O(skills × triggers).
"""
from __future__ import annotations

import re
import yaml
from pathlib import Path
from typing import Any

from app.schemas import Skill


_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n?(.*)$",
    re.DOTALL | re.MULTILINE,
)


def _parse_skill_md(path: Path) -> Skill | None:
    """Parse a single ``skill.md`` file and return a ``Skill`` object."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None

    # Strip potential UTF-8 BOM that some editors leave.
    raw = raw.lstrip("\ufeff")

    match = _FRONTMATTER_RE.match(raw.strip())
    if not match:
        # No frontmatter — treat the whole file as prompt, name from directory.
        name = path.parent.name
        return Skill(
            name=name,
            description="",
            triggers=[],
            prompt=raw.strip(),
            dir_path=str(path.parent.resolve()),
        )

    frontmatter_text, body = match.group(1), match.group(2).strip()

    try:
        meta: dict[str, Any] = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError:
        return None

    name = meta.get("name") or path.parent.name
    return Skill(
        name=name,
        description=meta.get("description", ""),
        triggers=meta.get("triggers") or [],
        prompt=body,
        dir_path=str(path.parent.resolve()),
    )


class SkillLoader:
    """Discovers skills from ``settings.skills_dir`` and provides match API."""

    def __init__(self, skills_dir: Path | None = None) -> None:
        if skills_dir is None:
            # Resolve relative to agent-core root (three levels up from this file:
            # app/services/skill_loader.py → app → agent-core/).
            skills_dir = Path(__file__).resolve().parents[2] / "skills"
        self.skills_dir = skills_dir
        self._skills: list[Skill] = []
        self._trigger_index: dict[str, list[Skill]] = {}
        self._loaded = False

    # ---- Public API ------------------------------------------------------ #

    def list_skills(self) -> list[Skill]:
        self._ensure_loaded()
        return list(self._skills)

    def match(self, message: str) -> list[Skill]:
        """Return all skills whose triggers appear in ``message`` (case-insensitive)."""
        self._ensure_loaded()
        if not message:
            return []
        lowered = message.lower()
        hits: list[Skill] = []
        seen: set[str] = set()
        for trigger, skills in self._trigger_index.items():
            if trigger in lowered:
                for skill in skills:
                    if skill.name not in seen:
                        hits.append(skill)
                        seen.add(skill.name)
        return hits

    def get_prompts_for_message(self, message: str) -> list[str]:
        """Return the ``prompt`` fragments of all matched skills, in load order."""
        return [s.prompt for s in self.match(message) if s.prompt]

    # ---- Internal -------------------------------------------------------- #

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._skills.clear()
        self._trigger_index.clear()

        if not self.skills_dir.exists():
            self._loaded = True
            return

        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "skill.md"
            if not skill_md.exists():
                continue
            skill = _parse_skill_md(skill_md)
            if skill is None:
                continue
            self._skills.append(skill)

        # Build trigger → [skills] index.
        for skill in self._skills:
            for trigger in skill.triggers:
                self._trigger_index.setdefault(trigger.lower(), []).append(skill)

        self._loaded = True
