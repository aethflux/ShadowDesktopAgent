from __future__ import annotations

from typing import Any

from app.config import settings
from app.services.mcp_client import MCPClient
from app.tools.base import Tool
from app.tools.cli import ExternalCLITool
from app.tools.gui import GuiAutomationTool
from app.tools.mcp_admin import MCPServerListTool, MCPToolListTool
from app.tools.mcp_policy import is_mcp_tool_allowed
from app.tools.mcp_tool import MCPBridgeTool
from app.tools.screen import ScreenReaderTool
from app.tools.skills import SkillCreateTool, SkillInstallFromUrlTool, SkillListTool
from app.tools.terminal import TerminalResetTool, TerminalTool


class ToolRegistry:
    def __init__(self, mcp_client: MCPClient | None = None) -> None:
        tools: list[Tool] = [
            TerminalTool(),
            TerminalResetTool(),
            ExternalCLITool(),
            SkillListTool(),
            SkillCreateTool(),
            SkillInstallFromUrlTool(),
            ScreenReaderTool(),
        ]
        if mcp_client is not None:
            tools.extend([MCPServerListTool(mcp_client), MCPToolListTool(mcp_client)])
        if settings.enable_gui_automation:
            tools.append(GuiAutomationTool())
        self._tools: dict[str, Tool] = {tool.name: tool for tool in tools}

    def specs(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in self._tools.values()
        ]

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    async def arun(self, name: str, args: dict[str, Any]) -> str:
        return await self._tools[name].arun(**args)

    def run(self, name: str, args: dict[str, Any]) -> str:
        """Synchronous fallback for tools that don't need ``await``."""
        return self._tools[name].run(**args)

    async def load_mcp_tools(self, mcp_client: MCPClient) -> int:
        """Discover tools from every registered MCP server and expose them.

        Returns the number of tools added. Silently ignores servers that fail
        to start — a missing ``npx`` or server binary should not crash the
        entire agent on boot.
        """
        added = 0
        for server_info in mcp_client.list_servers():
            server_name = server_info["name"]
            try:
                tools = await mcp_client.list_tools(server_name)
            except Exception:
                continue
            for tool in tools:
                tool_name = tool.get("name", "")
                if not is_mcp_tool_allowed(tool_name):
                    continue
                bridge = MCPBridgeTool(
                    mcp_client=mcp_client,
                    server_name=server_name,
                    tool_name=tool_name,
                    description=tool.get("description") or f"MCP tool from {server_name}",
                    input_schema=tool.get("inputSchema") or {"type": "object", "properties": {}},
                )
                self.register(bridge)
                added += 1
        return added
