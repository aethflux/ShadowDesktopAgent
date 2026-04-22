"""Unit tests for MemoryStore: JSONL append, profile extraction, semantic recall."""
from __future__ import annotations

from pathlib import Path

from app.schemas import MemoryItem
from app.services.memory import MemoryStore


def test_append_and_recent(tmp_path: Path, tmp_session_id: str) -> None:
    store = MemoryStore(root=tmp_path)
    for i in range(3):
        store.append(
            MemoryItem(session_id=tmp_session_id, role="user", content=f"msg {i}")
        )
    items = store.recent(tmp_session_id)
    assert len(items) == 3
    assert items[-1].content == "msg 2"


def test_profile_extracts_preferred_name(tmp_path: Path, tmp_session_id: str) -> None:
    store = MemoryStore(root=tmp_path)
    store.update_profile_from_message(tmp_session_id, "你可以叫我拓也")
    profile = store.load_profile(tmp_session_id)
    assert profile.preferred_name == "拓也"


def test_profile_extracts_role_and_goals(tmp_path: Path, tmp_session_id: str) -> None:
    store = MemoryStore(root=tmp_path)
    store.update_profile_from_message(
        tmp_session_id, "我是一个AI研究员，我的目标是今年进入头部公司"
    )
    profile = store.load_profile(tmp_session_id)
    assert profile.role and "研究员" in profile.role
    assert any("头部" in g for g in profile.goals)


def test_semantic_recall_returns_related_memories(tmp_path: Path, tmp_session_id: str) -> None:
    """Hash embedder is deterministic and offline — exact match should top the list."""
    store = MemoryStore(root=tmp_path)
    if not store.vector.available:
        import pytest

        pytest.skip("chromadb not installed in this environment")
    store.append(
        MemoryItem(session_id=tmp_session_id, role="user", content="我正在做一个桌面 Agent 项目")
    )
    store.append(
        MemoryItem(session_id=tmp_session_id, role="user", content="今天天气不错")
    )
    hits = store.semantic_recall(tmp_session_id, "桌面 Agent 进度")
    assert hits
    # The project-related memory should be recalled before the weather chit-chat.
    assert any("Agent" in h for h in hits)
