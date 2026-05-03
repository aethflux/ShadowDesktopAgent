from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

from app.config import settings
from app.services.skill_loader import SkillLoader
from app.tools.base import Tool


_SKILL_SLUG_RE = re.compile(r"[^a-z0-9_-]+")
_MAX_SKILL_BYTES = 128 * 1024


def _slugify_skill_name(name: str) -> str:
    slug = _SKILL_SLUG_RE.sub("-", name.strip().lower()).strip("-_")
    return slug[:64] or "custom-skill"


def _safe_skill_dir(name: str) -> Path:
    root = settings.skills_dir.expanduser().resolve()
    target = (root / _slugify_skill_name(name)).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"skill path outside skills dir: {target}") from exc
    return target


def _skill_markdown(name: str, description: str, triggers: list[str], prompt: str) -> str:
    frontmatter = yaml.safe_dump(
        {
            "name": name,
            "description": description,
            "triggers": triggers,
        },
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    return f"---\n{frontmatter}\n---\n{prompt.strip()}\n"


class SkillListTool(Tool):
    name = "skill.list"
    description = "List installed prompt skills and their triggers."
    input_schema = {"type": "object", "properties": {}}

    def run(self, **kwargs: Any) -> str:
        skills = SkillLoader(settings.skills_dir).list_skills()
        if not skills:
            return "No skills installed."
        lines = []
        for skill in skills:
            triggers = ", ".join(skill.triggers) or "none"
            lines.append(f"- {skill.name}: {skill.description or 'no description'} | triggers: {triggers}")
        return "\n".join(lines)


class SkillCreateTool(Tool):
    name = "skill.create"
    description = (
        "Create or update a local prompt skill under the controlled skills directory. "
        "This writes only a skill.md prompt file; it never executes code."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "triggers": {"type": "array", "items": {"type": "string"}},
            "prompt": {"type": "string"},
            "overwrite": {"type": "boolean"},
        },
        "required": ["name", "prompt"],
    }

    def run(self, **kwargs: Any) -> str:
        raw_name = str(kwargs["name"]).strip()
        prompt = str(kwargs["prompt"]).strip()
        if not prompt:
            return "Tool skill.create failed: prompt is empty"
        skill_name = _slugify_skill_name(raw_name)
        description = str(kwargs.get("description") or "")
        raw_triggers = kwargs.get("triggers") or []
        if not isinstance(raw_triggers, list):
            return "Tool skill.create failed: triggers must be a list"
        triggers = [str(trigger).strip().lower() for trigger in raw_triggers if str(trigger).strip()]
        overwrite = bool(kwargs.get("overwrite"))

        skill_dir = _safe_skill_dir(skill_name)
        skill_file = skill_dir / "skill.md"
        if skill_file.exists() and not overwrite:
            return f"Tool skill.create failed: skill already exists: {skill_name}; pass overwrite=true to replace it"

        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(
            _skill_markdown(skill_name, description, triggers, prompt),
            encoding="utf-8",
        )
        return f"Created skill '{skill_name}' at {skill_file}"


class SkillInstallFromUrlTool(Tool):
    name = "skill.install_from_url"
    description = (
        "Download a remote markdown prompt skill into the controlled skills directory. "
        "Only the skill.md text is stored; downloaded content is not executed."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "name": {"type": "string"},
            "overwrite": {"type": "boolean"},
        },
        "required": ["url"],
    }

    def run(self, **kwargs: Any) -> str:  # pragma: no cover - async path used
        raise RuntimeError("skill.install_from_url must be invoked asynchronously")

    async def arun(self, **kwargs: Any) -> str:
        url = str(kwargs["url"]).strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return "Tool skill.install_from_url failed: only http/https URLs are allowed"

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            content = response.content
        if len(content) > _MAX_SKILL_BYTES:
            return f"Tool skill.install_from_url failed: remote skill is too large ({len(content)} bytes)"

        text = content.decode("utf-8", errors="replace").lstrip("\ufeff").strip()
        if not text:
            return "Tool skill.install_from_url failed: remote skill is empty"

        inferred_name = kwargs.get("name") or Path(parsed.path).stem or parsed.netloc
        skill_name = _slugify_skill_name(str(inferred_name))
        skill_dir = _safe_skill_dir(skill_name)
        skill_file = skill_dir / "skill.md"
        if skill_file.exists() and not bool(kwargs.get("overwrite")):
            return f"Tool skill.install_from_url failed: skill already exists: {skill_name}; pass overwrite=true to replace it"

        if not text.startswith("---"):
            text = _skill_markdown(
                skill_name,
                f"Imported from {url}",
                [skill_name],
                text,
            )

        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(text.rstrip() + "\n", encoding="utf-8")
        return f"Installed remote skill '{skill_name}' at {skill_file}"
