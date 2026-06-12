from __future__ import annotations

from app.agents.companion import CompanionAgent
from app.agents.terminal_agent import TerminalAgent
from app.tools.registry import ToolRegistry


def test_companion_agent_exposes_small_tool_context() -> None:
    registry = ToolRegistry()

    names = CompanionAgent().tool_names_for_turn(registry)

    assert "skill.list" in names
    assert "image.generate" in names
    assert "terminal.run" not in names
    assert "cli.run" not in names


def test_terminal_agent_exposes_execution_tools() -> None:
    registry = ToolRegistry()

    names = TerminalAgent().tool_names_for_turn(registry)

    assert "terminal.run" in names
    assert "cli.run" in names
    assert "skill.install_from_url" in names
