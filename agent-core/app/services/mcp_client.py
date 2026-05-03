"""Minimal Model Context Protocol (MCP) client with real stdio JSON-RPC.

Implements just enough of the MCP spec to actually call tools on an external
MCP server (e.g. ``@modelcontextprotocol/server-filesystem``). The goal is to
demonstrate end-to-end tool invocation rather than to cover the full protocol.

Lifecycle
---------
1. ``register_server(name, command, args)`` records how to launch a server.
2. ``start(name)`` spawns the subprocess and performs the ``initialize``
   handshake.
3. ``list_tools(name)`` fetches the tool catalog (cached).
4. ``call_tool(name, tool_name, arguments)`` invokes a tool and returns its
   textual output.
5. ``shutdown()`` terminates all running server subprocesses.

All JSON-RPC traffic is framed as newline-delimited JSON on the child process's
stdio — which is what MCP servers over stdio expect.

Concurrency: a per-server ``asyncio.Lock`` serializes requests so that
interleaved reads don't corrupt the response stream. Good enough for the
single-user desktop-agent setting.
"""
from __future__ import annotations

import asyncio
import json
import shutil
from typing import Any


class MCPServerProcess:
    """Wraps a single running MCP server subprocess."""

    def __init__(self, name: str, command: str, args: list[str]) -> None:
        self.name = name
        self.command = command
        self.args = args
        self.proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._next_id = 0
        self._initialized = False
        self._tools_cache: list[dict[str, Any]] | None = None

    async def start(self) -> None:
        if self.proc is not None:
            return
        resolved = shutil.which(self.command) or self.command
        self.proc = await asyncio.create_subprocess_exec(
            resolved,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "hoshino-agent", "version": "0.1.0"},
            },
        )
        # MCP requires a `notifications/initialized` after the handshake.
        await self._notify("notifications/initialized", {})
        self._initialized = True

    async def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.proc is None or self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError(f"MCP server '{self.name}' is not running")
        async with self._lock:
            self._next_id += 1
            request = {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": method,
                "params": params,
            }
            payload = (json.dumps(request) + "\n").encode("utf-8")
            self.proc.stdin.write(payload)
            await self.proc.stdin.drain()

            # Read lines until we find a matching response id.
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    stderr = ""
                    if self.proc.stderr is not None:
                        stderr_bytes = await self.proc.stderr.read()
                        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
                    detail = f": {stderr[:1000]}" if stderr else ""
                    raise RuntimeError(f"MCP server '{self.name}' closed unexpectedly{detail}")
                try:
                    message = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                if message.get("id") == self._next_id:
                    if "error" in message:
                        raise RuntimeError(f"MCP error: {message['error']}")
                    return message.get("result") or {}

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        if self.proc is None or self.proc.stdin is None:
            return
        request = {"jsonrpc": "2.0", "method": method, "params": params}
        self.proc.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
        await self.proc.stdin.drain()

    async def list_tools(self) -> list[dict[str, Any]]:
        if self._tools_cache is not None:
            return self._tools_cache
        if not self._initialized:
            await self.start()
        result = await self._rpc("tools/list", {})
        self._tools_cache = result.get("tools") or []
        return self._tools_cache

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if not self._initialized:
            await self.start()
        result = await self._rpc(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
        )
        # MCP returns a content array; flatten to text for our tool interface.
        parts: list[str] = []
        for block in result.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        if result.get("isError"):
            return f"MCP tool error: {' '.join(parts) or 'unknown error'}"
        return "\n".join(parts).strip() or "(no output)"

    async def stop(self) -> None:
        if self.proc is None:
            return
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
                try:
                    await self.proc.stdin.wait_closed()
                except Exception:
                    pass
            self.proc.terminate()
            await asyncio.wait_for(self.proc.wait(), timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc = None
        self._initialized = False
        self._tools_cache = None


class MCPClient:
    """Registry + lifecycle manager for multiple MCP servers."""

    def __init__(self) -> None:
        self.servers: dict[str, MCPServerProcess] = {}

    def register_server(self, name: str, command: str, args: list[str] | None = None) -> None:
        self.servers[name] = MCPServerProcess(name, command, args or [])

    def list_servers(self) -> list[dict[str, Any]]:
        return [
            {
                "name": server.name,
                "command": server.command,
                "args": server.args,
                "running": server.proc is not None,
            }
            for server in self.servers.values()
        ]

    async def ensure_started(self, name: str) -> MCPServerProcess:
        server = self.servers.get(name)
        if server is None:
            raise KeyError(f"MCP server '{name}' is not registered")
        await server.start()
        return server

    async def list_tools(self, name: str) -> list[dict[str, Any]]:
        server = await self.ensure_started(name)
        return await server.list_tools()

    async def call_tool(self, name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        server = await self.ensure_started(name)
        return await server.call_tool(tool_name, arguments)

    async def shutdown(self) -> None:
        for server in self.servers.values():
            await server.stop()
