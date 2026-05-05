"""End-to-end verification for the GitHub-skill long-task path.

Goal: prove that a remote skill can be downloaded, parsed, indexed, and then
matched against a user message — the same chain a real "下载 GitHub skill 并跑
长任务" turn would walk through.

We mock ``httpx`` so the test stays offline and deterministic; the rest of
the chain (SkillInstallFromUrlTool → filesystem write → SkillLoader →
ContextManager match) runs for real against a tmp ``skills_dir``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from app.config import settings
from app.services.skill_loader import SkillLoader
from app.tools.skills import SkillInstallFromUrlTool


_SAMPLE_SKILL_WITH_FRONTMATTER = """---
name: pdf-extractor
description: Extract text and tables from PDF files
triggers:
  - pdf
  - extract
  - parse
---
You are a PDF processing specialist. When asked to read a PDF, walk through
the file page by page and surface its structure. Quote exact passages where
possible and prefer tables over prose where the source supports it.
"""


_SAMPLE_SKILL_WITHOUT_FRONTMATTER = """# Code Review Skill

Walk through diffs systematically: correctness first, then performance,
then style. Flag every assertion that depends on out-of-diff context.
"""


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.content = body
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None


class _FakeAsyncClient:
    """Drop-in replacement for ``httpx.AsyncClient`` used by the install tool.

    We capture the URL on .get() so tests can assert which remote was hit
    without setting up a real server. ``__aenter__`` / ``__aexit__`` mirror
    the async-context-manager surface the tool code uses.
    """

    def __init__(self, body: bytes) -> None:
        self._body = body
        self.last_url: str | None = None

    def __call__(self, *args: Any, **kwargs: Any) -> "_FakeAsyncClient":
        # ``httpx.AsyncClient(timeout=15, follow_redirects=True)`` returns the
        # client itself; mimic that for use with ``async with``.
        return self

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get(self, url: str) -> _FakeResponse:
        self.last_url = url
        return _FakeResponse(self._body)


@pytest.fixture
def isolated_skills_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``settings.skills_dir`` at a temp directory so the test's
    installed skill doesn't pollute the real ``agent-core/skills``."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(settings, "skills_dir", skills_dir)
    return skills_dir


@pytest.mark.asyncio
async def test_install_skill_with_frontmatter_writes_file(
    isolated_skills_dir: Path,
) -> None:
    """A remote skill that already has YAML frontmatter must land verbatim
    (after a strip + trailing newline) — no double-wrapping."""
    fake = _FakeAsyncClient(_SAMPLE_SKILL_WITH_FRONTMATTER.encode("utf-8"))
    tool = SkillInstallFromUrlTool()

    with patch("app.tools.skills.httpx.AsyncClient", fake):
        result = await tool.arun(
            url="https://raw.githubusercontent.com/example/skills/main/pdf.md",
            name="pdf-extractor",
        )

    assert "Installed remote skill 'pdf-extractor'" in result
    skill_file = isolated_skills_dir / "pdf-extractor" / "skill.md"
    assert skill_file.exists()
    text = skill_file.read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "PDF processing specialist" in text
    assert fake.last_url == "https://raw.githubusercontent.com/example/skills/main/pdf.md"


@pytest.mark.asyncio
async def test_install_skill_without_frontmatter_gets_wrapped(
    isolated_skills_dir: Path,
) -> None:
    """A bare markdown file must get auto-wrapped with a frontmatter so the
    SkillLoader can read it as a regular skill."""
    fake = _FakeAsyncClient(_SAMPLE_SKILL_WITHOUT_FRONTMATTER.encode("utf-8"))
    tool = SkillInstallFromUrlTool()

    with patch("app.tools.skills.httpx.AsyncClient", fake):
        result = await tool.arun(
            url="https://raw.githubusercontent.com/example/skills/main/code-review.md",
        )

    assert "Installed remote skill" in result
    # The slug comes from the URL stem when ``name`` isn't passed.
    expected_dir = isolated_skills_dir / "code-review"
    skill_file = expected_dir / "skill.md"
    assert skill_file.exists()
    text = skill_file.read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "name: code-review" in text
    assert "Code Review Skill" in text


@pytest.mark.asyncio
async def test_installed_skill_is_discoverable_by_loader(
    isolated_skills_dir: Path,
) -> None:
    """End-to-end glue check: install via tool, then load via SkillLoader.

    This is the chain a real chat turn walks through — the agent installs a
    remote skill, ContextManager reloads, then the next message that hits
    one of its trigger keywords gets the skill prompt injected. We exercise
    everything except the LLM call itself.
    """
    fake = _FakeAsyncClient(_SAMPLE_SKILL_WITH_FRONTMATTER.encode("utf-8"))
    tool = SkillInstallFromUrlTool()
    with patch("app.tools.skills.httpx.AsyncClient", fake):
        await tool.arun(
            url="https://raw.githubusercontent.com/example/skills/main/pdf.md",
            name="pdf-extractor",
        )

    loader = SkillLoader(isolated_skills_dir)
    skills = loader.list_skills()
    pdf_skills = [s for s in skills if s.name == "pdf-extractor"]
    assert len(pdf_skills) == 1
    skill = pdf_skills[0]
    assert "extract" in skill.triggers
    assert "PDF processing specialist" in skill.prompt

    # The trigger index must surface this skill on a matching message.
    matched = loader.match("帮我 extract 一份发票 PDF")
    assert any(s.name == "pdf-extractor" for s in matched)


@pytest.mark.asyncio
async def test_install_rejects_non_http_url(isolated_skills_dir: Path) -> None:
    """``file://`` and other schemes must be rejected — the tool is only meant
    for fetching public HTTP(S) markdown."""
    tool = SkillInstallFromUrlTool()
    result = await tool.arun(url="file:///etc/passwd")
    assert "only http/https" in result


@pytest.mark.asyncio
async def test_install_refuses_oversized_payload(
    isolated_skills_dir: Path,
) -> None:
    """Remote skills capped at 128 KiB. A 200 KiB body must be rejected
    *before* anything hits disk."""
    huge = b"# big\n" + (b"a" * (200 * 1024))
    fake = _FakeAsyncClient(huge)
    tool = SkillInstallFromUrlTool()
    with patch("app.tools.skills.httpx.AsyncClient", fake):
        result = await tool.arun(
            url="https://raw.githubusercontent.com/example/skills/main/big.md",
        )
    assert "too large" in result
    assert not (isolated_skills_dir / "big" / "skill.md").exists()
