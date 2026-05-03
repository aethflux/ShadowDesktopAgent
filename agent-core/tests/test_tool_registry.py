"""ToolRegistry tests — happy path, missing-tool fallback, sync/async parity."""
from __future__ import annotations

import asyncio

from app.tools.base import Tool
from app.tools.registry import ToolRegistry


class _EchoTool(Tool):
    name = "echo"
    description = "echoes its input"
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    def run(self, **kwargs: object) -> str:
        return f"echo:{kwargs.get('text', '')}"


def test_registry_lists_default_tools() -> None:
    registry = ToolRegistry()
    names = registry.names()
    # ``gui.act`` is now gated behind ``enable_gui_automation``; the rest are always on.
    for expected in ("terminal.run", "terminal.reset", "screen.capture"):
        assert expected in names, f"missing built-in tool: {expected}"


def test_registry_register_and_run() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool())
    assert registry.has("echo")
    assert registry.run("echo", {"text": "hi"}) == "echo:hi"


def test_registry_missing_tool_returns_friendly_message() -> None:
    """Unknown tool names must not raise — they should surface a usable error."""
    registry = ToolRegistry()
    result = registry.run("does_not_exist", {})
    assert "not registered" in result.lower()
    assert "available tools" in result.lower()


def test_registry_arun_missing_tool_async() -> None:
    registry = ToolRegistry()
    result = asyncio.run(registry.arun("ghost.tool", {}))
    assert "not registered" in result.lower()


def test_registry_has_returns_false_for_unknown() -> None:
    registry = ToolRegistry()
    assert not registry.has("nonexistent")
    assert registry.has("screen.capture")
