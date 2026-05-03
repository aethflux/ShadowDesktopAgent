"""Verify TTS audio retention sweep keeps fresh files and removes stale ones."""
from __future__ import annotations

import os
import time
from pathlib import Path

from app.main import _cleanup_old_audio


def test_cleanup_removes_only_stale_files(tmp_path: Path) -> None:
    fresh = tmp_path / "fresh.mp3"
    stale = tmp_path / "stale.mp3"
    fresh.write_bytes(b"new")
    stale.write_bytes(b"old")

    # Backdate the stale file to 48 hours ago.
    stale_time = time.time() - 48 * 3600
    os.utime(stale, (stale_time, stale_time))

    removed = _cleanup_old_audio(tmp_path, retention_hours=24)
    assert removed == 1
    assert fresh.exists()
    assert not stale.exists()


def test_cleanup_skips_when_retention_disabled(tmp_path: Path) -> None:
    file = tmp_path / "ancient.mp3"
    file.write_bytes(b"x")
    os.utime(file, (0, 0))  # epoch — definitely "old"
    removed = _cleanup_old_audio(tmp_path, retention_hours=0)
    assert removed == 0
    assert file.exists()


def test_cleanup_handles_missing_directory(tmp_path: Path) -> None:
    removed = _cleanup_old_audio(tmp_path / "does-not-exist", retention_hours=24)
    assert removed == 0
