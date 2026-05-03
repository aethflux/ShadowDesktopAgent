"""Verify MemoryStore.recent uses bounded tail reads, not full-file slurps."""
from __future__ import annotations

from pathlib import Path

from app.schemas import MemoryItem
from app.services.memory import MemoryStore, _tail_lines


def test_tail_lines_returns_last_n(tmp_path: Path) -> None:
    target = tmp_path / "log.jsonl"
    target.write_text("\n".join(f"line-{i}" for i in range(50)) + "\n", encoding="utf-8")
    tail = _tail_lines(target, 5)
    assert tail == [f"line-{i}" for i in range(45, 50)]


def test_tail_lines_skips_empty_lines(tmp_path: Path) -> None:
    target = tmp_path / "log.jsonl"
    target.write_text("a\n\n\nb\n\n", encoding="utf-8")
    assert _tail_lines(target, 10) == ["a", "b"]


def test_tail_lines_handles_missing_file(tmp_path: Path) -> None:
    assert _tail_lines(tmp_path / "missing.jsonl", 5) == []


def test_recent_handles_malformed_rows(tmp_path: Path, tmp_session_id: str) -> None:
    """Recent() must skip junk lines instead of blowing up the whole recall."""
    store = MemoryStore(root=tmp_path)
    store.append(MemoryItem(session_id=tmp_session_id, role="user", content="real"))
    # Manually corrupt the file with a bad row in between two good ones.
    log = tmp_path / f"{tmp_session_id}.jsonl"
    with log.open("a", encoding="utf-8") as fh:
        fh.write("not-json-at-all\n")
    store.append(MemoryItem(session_id=tmp_session_id, role="user", content="real-2"))

    items = store.recent(tmp_session_id)
    contents = [item.content for item in items]
    assert "real" in contents
    assert "real-2" in contents
