"""Tool adapters that bridge local tool-calling into MCP servers.

Each ``MCPBridgeTool`` wraps a single MCP tool so the LLM can call it using the
same OpenAI-style function schema as any other local tool. This is how Hoshino
actually demonstrates MCP end-to-end — the LLM doesn't know or care that the
tool is remote.
"""
from __future__ import annotations

from typing import Any

from app.services.mcp_client import MCPClient
from app.tools.base import Tool


class MCPBridgeTool(Tool):
    def __init__(
        self,
        mcp_client: MCPClient,
        server_name: str,
        tool_name: str,
        description: str,
        input_schema: dict[str, Any],
        exposed_name: str | None = None,
    ) -> None:
        self._mcp = mcp_client
        self._server_name = server_name
        self._tool_name = tool_name
        self.name = exposed_name or f"mcp.{server_name}.{tool_name}"
        self.description = description
        self.input_schema = input_schema or {"type": "object", "properties": {}}

    def run(self, **kwargs: Any) -> str:  # pragma: no cover - sync path unused
        raise RuntimeError(
            f"{self.name} must be invoked via the async registry path; "
            "it talks to an MCP server over stdio JSON-RPC."
        )

    async def arun(self, **kwargs: Any) -> str:
        try:
            return await self._mcp.call_tool(self._server_name, self._tool_name, kwargs)
        except Exception as exc:
            return f"MCP tool {self.name} failed: {exc}"
