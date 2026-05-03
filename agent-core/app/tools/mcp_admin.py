from __future__ import annotations

from typing import Any

from app.services.mcp_client import MCPClient
from app.tools.base import Tool
from app.tools.mcp_policy import mcp_tool_policy_label


class MCPServerListTool(Tool):
    name = "mcp.servers"
    description = "List registered MCP servers and their launch status."
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, mcp_client: MCPClient) -> None:
        self._mcp = mcp_client

    def run(self, **kwargs: Any) -> str:
        servers = self._mcp.list_servers()
        if not servers:
            return "No MCP servers registered."
        lines = []
        for server in servers:
            args = " ".join(server.get("args") or [])
            status = "running" if server.get("running") else "stopped"
            lines.append(f"- {server['name']}: {server['command']} {args}".strip() + f" ({status})")
        return "\n".join(lines)


class MCPToolListTool(Tool):
    name = "mcp.list_tools"
    description = "List tools exposed by a registered MCP server, or by all servers."
    input_schema = {
        "type": "object",
        "properties": {
            "server_name": {"type": "string"},
        },
    }

    def __init__(self, mcp_client: MCPClient) -> None:
        self._mcp = mcp_client

    def run(self, **kwargs: Any) -> str:  # pragma: no cover - async path used
        raise RuntimeError("mcp.list_tools must be invoked asynchronously")

    async def arun(self, **kwargs: Any) -> str:
        requested = kwargs.get("server_name")
        servers = self._mcp.list_servers()
        if requested:
            servers = [server for server in servers if server["name"] == requested]
        if not servers:
            return f"No MCP server matched: {requested}" if requested else "No MCP servers registered."

        lines: list[str] = []
        for server in servers:
            name = server["name"]
            try:
                tools = await self._mcp.list_tools(name)
            except Exception as exc:
                lines.append(f"[{name}] failed: {exc}")
                continue
            if not tools:
                lines.append(f"[{name}] no tools")
                continue
            for tool in tools:
                tool_name = tool.get("name", "<unnamed>")
                lines.append(
                    f"[{name}] {tool_name} ({mcp_tool_policy_label(tool_name)}): {tool.get('description', '')}"
                )
        return "\n".join(lines)
