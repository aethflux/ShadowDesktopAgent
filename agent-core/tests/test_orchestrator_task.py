"""Verify the orchestrator builds task records using both the explicit success
field and heuristic stdout markers (e.g. ``exit=N``, ``blocked unsafe command``).
"""
from __future__ import annotations

from app.orchestrator import MultiAgentOrchestrator
from app.schemas import ChatRequest, ToolCallRecord


def _orchestrator() -> MultiAgentOrchestrator:
    # Construction is cheap and side-effect free for this test (no MCP bootstrap).
    return MultiAgentOrchestrator()


def test_explicit_failure_marks_step_failed() -> None:
    """When the agent records ``success=False`` (tool raised / unknown name),
    the step is failed regardless of stdout shape."""
    orch = _orchestrator()
    request = ChatRequest(message="hi")
    tool_calls = [
        ToolCallRecord(name="terminal.run", args={}, result="connection lost", success=False),
    ]
    task = orch._build_task(request, "terminal-agent", "ok", tool_calls)
    assert task["steps"][0]["status"] == "failed"


def test_clean_success_marks_step_completed() -> None:
    """``success=True`` plus output that doesn't trip the heuristic stays completed."""
    orch = _orchestrator()
    request = ChatRequest(message="hi")
    tool_calls = [
        ToolCallRecord(
            name="terminal.run",
            args={},
            result="hello world",
            success=True,
        ),
    ]
    task = orch._build_task(request, "terminal-agent", "done", tool_calls)
    assert task["steps"][0]["status"] == "completed"


def test_heuristic_catches_nonzero_exit_code() -> None:
    """Even if the agent set ``success=True``, an ``exit=N`` (N != 0) marker in
    stdout flips the step to failed — terminals can succeed at the syscall level
    but report a non-zero return code we want surfaced to the UI."""
    orch = _orchestrator()
    request = ChatRequest(message="hi")
    tool_calls = [
        ToolCallRecord(
            name="terminal.run",
            args={},
            result="ran command, exit=2",
            success=True,
        ),
    ]
    task = orch._build_task(request, "terminal-agent", "done", tool_calls)
    assert task["steps"][0]["status"] == "failed"


def test_heuristic_catches_mcp_tool_error() -> None:
    orch = _orchestrator()
    request = ChatRequest(message="hi")
    tool_calls = [
        ToolCallRecord(
            name="mcp.filesystem.list_directory",
            args={"path": "E:\\agent\\zhuochong"},
            result=(
                "MCP tool error: Access denied - path outside allowed directories: "
                "E:\\agent\\zhuochong not in E:\\agent\\zhuochong\\agent-core\\skills"
            ),
            success=True,
        ),
    ]

    task = orch._build_task(request, "terminal-agent", "done", tool_calls)

    assert task["status"] == "failed"
    assert task["steps"][0]["status"] == "failed"
    assert task["steps"][0]["args"] == {"path": "E:\\agent\\zhuochong"}


def test_heuristic_marks_policy_block_as_blocked() -> None:
    orch = _orchestrator()
    request = ChatRequest(message="hi")
    tool_calls = [
        ToolCallRecord(
            name="cli.run",
            args={"command": "cmd"},
            result="Tool cli.run blocked: command 'cmd' is not allowlisted.",
            success=False,
        ),
    ]

    task = orch._build_task(request, "terminal-agent", "done", tool_calls)

    assert task["status"] == "blocked"
    assert task["steps"][0]["status"] == "blocked"


def test_empty_tool_calls_yields_direct_response_step() -> None:
    orch = _orchestrator()
    request = ChatRequest(message="hello")
    task = orch._build_task(request, "companion-agent", "hi back", [])
    assert task["step_count"] == 1
    assert task["steps"][0]["title"] == "direct-response"
