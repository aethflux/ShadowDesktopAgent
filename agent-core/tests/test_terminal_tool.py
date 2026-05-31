from __future__ import annotations

import asyncio
from pathlib import Path

from app.schemas import TerminalSessionState, ToolCallRecord
from app.agents.llm_agent import LLMAgent
from app.tools.registry import ToolRegistry
from app.tools.terminal import TerminalTool


def test_cd_without_target_reports_current_directory() -> None:
    tool = TerminalTool()
    cwd = Path.cwd()
    state = TerminalSessionState(session_id="test-cd", cwd=str(cwd))

    assert tool._handle_cd("cd", cwd, state) == str(cwd)
    assert state.cwd == str(cwd)


def test_compound_cd_command_is_left_to_shell() -> None:
    tool = TerminalTool()
    cwd = Path.cwd()
    state = TerminalSessionState(session_id="test-compound-cd", cwd=str(cwd))

    assert tool._handle_cd(f"cd {cwd} && git status --short", cwd, state) is None


def test_terminal_run_includes_exit_code() -> None:
    tool = TerminalTool()

    result = tool.run(
        command="echo terminal-ok",
        session_id="test-terminal-exit-code",
        reset_session=True,
    )

    assert "exit=0" in result
    assert "terminal-ok" in result


def test_invalid_cwd_reports_clear_error() -> None:
    tool = TerminalTool()
    missing = Path.cwd() / "__missing_terminal_cwd__"

    try:
        tool.run(command="echo nope", cwd=str(missing), session_id="test-invalid-cwd", reset_session=True)
    except FileNotFoundError as exc:
        assert "cwd not found" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")


def test_outside_workspace_cwd_is_blocked_via_broker() -> None:
    """Out-of-workspace cwd no longer hard-rejects synchronously: the
    permission broker now mediates. When called outside a streaming session
    (no progress_cb in the contextvar), the broker hard-denies, and ``arun``
    converts that into a structured tool-error string instead of raising."""
    tool = TerminalTool()
    outside = Path.home()
    if tool._is_path_allowed(outside):
        outside = Path.home().anchor

    result = asyncio.run(
        tool.arun(
            command="echo nope",
            cwd=str(outside),
            session_id="test-outside-cwd",
            reset_session=True,
        )
    )
    assert "Tool terminal.run blocked" in result, result


def test_destructive_commands_are_blocked() -> None:
    tool = TerminalTool()

    result = tool.run(
        command="Remove-Item -Recurse -Force .\\memory",
        session_id="test-block-remove",
        reset_session=True,
    )

    assert "blocked unsafe command" in result
    assert "delete commands are blocked" in result


def test_package_mutation_commands_are_blocked() -> None:
    tool = TerminalTool()

    result = tool.run(
        command="npm install left-pad",
        session_id="test-block-install",
        reset_session=True,
    )

    assert "blocked unsafe command" in result
    assert "package mutation commands are blocked" in result


def test_no_gui_automation_tool() -> None:
    # GUI automation was removed — the project never controls the real desktop.
    registry = ToolRegistry()

    assert "gui.act" not in registry.names()


def test_llm_agent_summarizes_tool_fallback() -> None:
    reply = LLMAgent._summarize_tool_fallback(
        [
            ToolCallRecord(
                name="terminal.run",
                args={"command": "git status --short"},
                result="[session=x] cwd=/repo exit=0\n M desktop/src/main.ts",
            )
        ]
    )

    assert "terminal.run" in reply
    assert "git status" not in reply
    assert "desktop/src/main.ts" in reply
