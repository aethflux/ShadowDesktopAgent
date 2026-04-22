"""Test configuration: isolate filesystem side-effects into a temp dir."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Redirect memory/skills/screenshot/chroma roots to a scratch dir BEFORE
# importing app.config. Tests must never touch the developer's real memory.
_TMP = Path(tempfile.mkdtemp(prefix="hoshino-tests-"))
os.environ.setdefault("MEMORY_DIR", str(_TMP / "memory"))
os.environ.setdefault("SKILLS_DIR", str(_TMP / "skills"))
os.environ.setdefault("SCREENSHOTS_DIR", str(_TMP / "screens"))
os.environ.setdefault("CHROMA_DIR", str(_TMP / "chroma"))
# Force offline provider defaults so nothing in the test suite hits the network.
os.environ.setdefault("PROVIDER", "vllm")
os.environ.setdefault("EMBEDDING_PROVIDER", "hash")


@pytest.fixture
def tmp_session_id() -> str:
    import uuid

    return f"test-{uuid.uuid4().hex[:8]}"
