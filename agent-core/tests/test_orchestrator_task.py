"""Verify the orchestrator builds task records using the explicit success field
rather than fragile string matching on tool output."""
from __future__ import annotations

from app.orchestrator import MultiAgentOrchestrator
from app.schemas import ChatRequest, ToolCallRecord


def _orchestrator() -> MultiAgentOrchestrator:
    # Construction is cheap and side-effect free for this test (no MCP bootstrap).
    return MultiAgentOrchestrator()


def test_failed_tool_marks_step_failed() -> None:
    orch = _orchestrator()
    request = ChatRequest(message="hi")
    tool_calls = [
        ToolCallRecord(name="terminal.run", args={}, result="connection lost", success=False),
    ]
    task = orch._build_task(request, "terminal-agent", "ok", tool_calls)
    assert task["steps"][0]["status"] == "failed"


def test_successful_tool_marks_step_completed_even_with_failed_word_in_output() -> None:
    """A tool can legitimately echo the word 'failed' (e.g. a test runner output);
    the step status must come from the success flag, not substring matching."""
    orch = _orchestrator()
    request = ChatRequest(message="hi")
    tool_calls = [
        ToolCallRecord(
            name="terminal.run",
            args={},
            result="3 tests failed: foo, bar, baz",  # legitimate stdout content
            success=True,
        ),
    ]
    task = orch._build_task(request, "terminal-agent", "done", tool_calls)
    assert task["steps"][0]["status"] == "completed"


def test_empty_tool_calls_yields_direct_response_step() -> None:
    orch = _orchestrator()
    request = ChatRequest(message="hello")
    task = orch._build_task(request, "companion-agent", "hi back", [])
    assert task["step_count"] == 1
    assert task["steps"][0]["title"] == "direct-response"
