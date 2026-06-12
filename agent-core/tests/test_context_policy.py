from __future__ import annotations

from pathlib import Path

from app.schemas import MemoryItem
from app.services.context_manager import ContextManager
from app.services.memory import MemoryStore
from app.services.skill_loader import SkillLoader


def _context_manager(tmp_path: Path) -> ContextManager:
    return ContextManager(
        MemoryStore(root=tmp_path / "memory"),
        SkillLoader(tmp_path / "skills"),
    )


def test_agent_context_policies_are_distinct(tmp_path: Path, tmp_session_id: str) -> None:
    manager = _context_manager(tmp_path)
    manager.memory_store.append(
        MemoryItem(session_id=tmp_session_id, role="user", content="我正在调试桌面 Agent 的 pytest 失败")
    )

    companion_context = manager.build_for_agent(
        "companion-agent",
        tmp_session_id,
        "聊聊刚才的问题",
        [],
    )
    terminal_context = manager.build_for_agent(
        "terminal-agent",
        tmp_session_id,
        "帮我跑 pytest",
        [],
    )
    desktop_context = manager.build_for_agent(
        "desktop-agent",
        tmp_session_id,
        "看看当前屏幕",
        [],
    )

    assert "Context policy for companion-agent" in companion_context
    assert "Context policy for terminal-agent" in terminal_context
    assert "Context policy for desktop-agent" in desktop_context
    assert "tool-related context" in terminal_context
    assert "visible screen state" in desktop_context


def test_router_context_excludes_memory_pack(tmp_path: Path) -> None:
    manager = _context_manager(tmp_path)

    context = manager.build_for_router(
        "帮我截图",
        [],
        ["screen.capture", "terminal.run"],
    )

    assert "User message: 帮我截图" in context
    assert "Available tools: screen.capture, terminal.run" in context
    assert "Recent memory" not in context
    assert "Semantically related memories" not in context
