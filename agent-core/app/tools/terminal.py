from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.config import settings
from app.schemas import TerminalSessionState
from app.services.memory import MemoryStore
from app.services.permission_broker import PermissionDenied, broker
from app.services.streaming_context import progress_cb_var
from app.tools.base import Tool


_BLOCKED_COMMAND_PATTERNS = (
    (re.compile(r"(^|[;&|]\s*)(rm|del|erase|rd|rmdir|remove-item|ri)\b", re.IGNORECASE), "delete commands are blocked"),
    (re.compile(r"\bgit\s+(reset\s+--hard|clean\b|checkout\s+--|restore\b|rm\b)", re.IGNORECASE), "destructive git commands are blocked"),
    (re.compile(r"\b(format|diskpart|bcdedit)\b", re.IGNORECASE), "disk/system modification commands are blocked"),
    (re.compile(r"\b(shutdown|restart-computer|stop-computer)\b", re.IGNORECASE), "power management commands are blocked"),
    (re.compile(r"\b(reg\s+delete|takeown|icacls|chmod|chown)\b", re.IGNORECASE), "permission/registry modification commands are blocked"),
    (re.compile(r"\b(set-content|add-content|out-file|new-item|move-item|copy-item|rename-item)\b", re.IGNORECASE), "file mutation commands are blocked"),
    (re.compile(r"(^|[^<])>{1,2}[^&]", re.IGNORECASE), "shell redirection is blocked"),
    (re.compile(r"\b(invoke-expression|iex|start-process)\b", re.IGNORECASE), "process execution helpers are blocked"),
    (re.compile(r"\b(npm|pnpm|yarn)\s+(install|add|update|upgrade|remove|uninstall)\b", re.IGNORECASE), "package mutation commands are blocked"),
    (re.compile(r"\b(pip|uv|poetry)\s+(install|add|remove|sync|lock)\b", re.IGNORECASE), "python package mutation commands are blocked"),
    (re.compile(r"\b(python|python3|py)\s+-m\s+(pip|uv)\s+(install|add|remove|sync|lock)\b", re.IGNORECASE), "python package mutation commands are blocked"),
    (re.compile(r"\b(winget|choco|scoop)\b", re.IGNORECASE), "system package managers are blocked"),
)


def unsafe_command_reason(command: str) -> str | None:
    command = command.strip()
    if not command:
        return "empty command"
    for pattern, reason in _BLOCKED_COMMAND_PATTERNS:
        if pattern.search(command):
            return reason
    return None


class TerminalTool(Tool):
    name = "terminal.run"
    description = "Run a shell command for coding and system tasks with session-aware working directory."
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "cwd": {"type": "string"},
            "session_id": {"type": "string"},
            "reset_session": {"type": "boolean"}
        },
        "required": ["command"]
    }

    def __init__(self) -> None:
        self.memory = MemoryStore()

    def _resolve_cwd(self, requested_cwd: str | None, state: TerminalSessionState) -> Path:
        """Resolve the requested working directory and assert it exists.

        Workspace boundary checks moved to :class:`PermissionBroker` (called
        from :meth:`arun`). This method now only ensures the path is real;
        approval is the broker's job.
        """
        if requested_cwd:
            path = Path(requested_cwd).expanduser().resolve()
        else:
            path = Path(state.cwd).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            raise FileNotFoundError(f"cwd not found: {path}; current session cwd={state.cwd}")
        return path

    @staticmethod
    def _is_relative_to(path: Path, root: Path) -> bool:
        """Kept for backwards compatibility with existing tests."""
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _is_path_allowed(self, path: Path) -> bool:
        """Kept for backwards compatibility with existing tests.

        Production code now flows through :class:`PermissionBroker`. This
        helper still answers "is this inside the legacy workspace root" so
        callers that pre-date the broker (and the test suite) keep working.
        """
        root = settings.command_workspace_root.expanduser().resolve()
        return self._is_relative_to(path.resolve(), root)

    @staticmethod
    def _unsafe_command_reason(command: str) -> str | None:
        return unsafe_command_reason(command)

    def _handle_cd(self, command: str, cwd: Path, state: TerminalSessionState) -> str | None:
        stripped = command.strip()
        lowered = stripped.lower()
        if lowered == "pwd":
            return str(cwd)
        if any(operator in stripped for operator in ("&&", "||", "|", ";", "\n")):
            return None
        if lowered == "cd":
            return str(cwd)
        if not lowered.startswith("cd "):
            return None

        target = stripped[2:].strip().strip('"')
        target_path = cwd / target if not Path(target).is_absolute() else Path(target)
        target_path = target_path.expanduser().resolve()
        if not target_path.exists() or not target_path.is_dir():
            return f"Directory not found: {target_path}"
        state.cwd = str(target_path)
        return f"Changed directory to {target_path}"

    def _run_shell(self, command: str, cwd: str) -> subprocess.CompletedProcess[str]:
        if os.name == "nt":
            shell_exe = shutil.which("pwsh") or shutil.which("powershell")
            if shell_exe:
                return subprocess.run(
                    [shell_exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=settings.command_timeout_seconds,
                    env=os.environ.copy(),
                )

        return subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=settings.command_timeout_seconds,
            env=os.environ.copy(),
        )

    async def _run_shell_streaming(
        self,
        command: str,
        cwd: str,
        step_id: str | None,
    ) -> tuple[int, str]:
        """Run a shell command and forward stdout line-by-line via SSE.

        Returns ``(exit_code, captured_output)``. The captured string is what
        gets fed back to the LLM; the SSE ``tool_progress`` events are purely
        for the user-facing log panel.

        Falls back to nothing-special when no progress callback is installed —
        the agent loop's ``tool_start`` / ``tool_end`` already cover the
        non-streaming case.
        """
        progress_cb = progress_cb_var.get()
        timeout = settings.command_timeout_seconds
        max_chars = max(1024, int(settings.terminal_stream_max_chars))

        if os.name == "nt":
            shell_exe = shutil.which("pwsh") or shutil.which("powershell")
            if shell_exe:
                process = await asyncio.create_subprocess_exec(
                    shell_exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command,
                    cwd=cwd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=os.environ.copy(),
                )
            else:
                process = await asyncio.create_subprocess_shell(
                    command,
                    cwd=cwd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=os.environ.copy(),
                )
        else:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=os.environ.copy(),
            )

        captured_parts: list[str] = []
        captured_len = 0
        truncated = False

        async def _drain() -> None:
            nonlocal captured_len, truncated
            assert process.stdout is not None
            while True:
                line_bytes = await process.stdout.readline()
                if not line_bytes:
                    return
                # ``errors='replace'`` keeps a single bad UTF-8 byte from
                # taking down the whole stream — common on Windows where the
                # console codepage may not be UTF-8.
                line = line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
                if captured_len < max_chars:
                    remaining = max_chars - captured_len
                    chunk = line[:remaining]
                    captured_parts.append(chunk)
                    captured_len += len(chunk)
                    if captured_len >= max_chars:
                        truncated = True
                # Always emit progress, even after the cap — UI keeps seeing
                # the live tail; we just stop growing the in-memory buffer.
                if progress_cb is not None:
                    try:
                        await progress_cb(
                            {
                                "event": "tool_progress",
                                "data": {
                                    "step_id": step_id,
                                    "stream": "stdout",
                                    "text": line,
                                    "truncated": truncated,
                                },
                            }
                        )
                    except Exception:  # pragma: no cover — defensive
                        pass

        drain_task = asyncio.create_task(_drain())
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await asyncio.wait_for(process.wait(), timeout=5)
            captured_parts.append(f"\n[terminal.run timeout after {timeout}s]")
            await drain_task
            return (124, "\n".join(captured_parts))

        await drain_task
        captured = "\n".join(captured_parts)
        if truncated:
            captured += f"\n[output truncated at {max_chars} chars]"
        return (process.returncode if process.returncode is not None else -1, captured)

    async def arun(self, **kwargs: Any) -> str:
        """Async entry point — asks the broker, then runs the command with
        live stdout streaming when an SSE session is active.

        On deny we return a structured tool-error string instead of raising —
        the agent loop surfaces this back to the LLM so it can explain the
        situation to the user without crashing. On approval, falls through to
        :meth:`_run_streaming_path`, which forwards each stdout line through
        ``progress_cb`` as a ``tool_progress`` SSE event.
        """
        session_id = str(kwargs.get("session_id") or "default")
        try:
            state = self.memory.load_terminal_session(session_id)
            target = self._resolve_cwd(kwargs.get("cwd"), state)
        except FileNotFoundError as exc:
            return f"[session={session_id}] Tool terminal.run failed: {exc}"

        try:
            await broker.check(
                target,
                reason=f"terminal.run wants to use cwd={target}",
                tool_name="terminal.run",
            )
        except PermissionDenied as exc:
            return f"[session={session_id}] Tool terminal.run blocked: {exc}"

        # If there's no streaming session, fall back to the legacy sync path —
        # it's slightly simpler and the test suite leans on it. With a session
        # we want the per-line tool_progress feed.
        if progress_cb_var.get() is None:
            return self.run(**kwargs)

        return await self._arun_streaming(state, target, **kwargs)

    async def _arun_streaming(
        self,
        state: TerminalSessionState,
        target: Path,
        **kwargs: Any,
    ) -> str:
        """Streaming variant of :meth:`run`. Honors the same safeguards
        (unsafe-command pattern, builtin ``cd``/``pwd``) but executes the
        actual command through :meth:`_run_shell_streaming` so each output
        line goes out as a ``tool_progress`` SSE event."""
        command = kwargs["command"]
        session_id = kwargs.get("session_id") or "default"
        reset_session = bool(kwargs.get("reset_session"))
        unsafe_reason = self._unsafe_command_reason(command)
        if unsafe_reason:
            return f"[session={session_id}] Tool terminal.run failed: blocked unsafe command: {unsafe_reason}"

        if reset_session:
            self.memory.reset_terminal_session(session_id)
            state = self.memory.load_terminal_session(session_id)

        state.cwd = str(target)
        builtin_result = self._handle_cd(command, target, state)
        state.history.append(command)
        state.history = state.history[-20:]
        if builtin_result is not None:
            self.memory.save_terminal_session(state)
            return f"[session={session_id}] {builtin_result}"

        # The orchestrator's tool_start/tool_end events use a step_id we don't
        # have here directly; passing ``None`` means tool_progress events
        # carry only the stream/text (still useful for live tailing). The
        # frontend correlates by ordering rather than by id.
        exit_code, output = await self._run_shell_streaming(
            command, state.cwd, step_id=kwargs.get("_step_id"),
        )
        text = output.strip() or "Command completed with no output."
        state.history.append(f"exit={exit_code}")
        state.history = state.history[-20:]
        self.memory.save_terminal_session(state)
        return f"[session={session_id}] cwd={state.cwd} exit={exit_code}\n{text[:4000]}"

    def run(self, **kwargs: str) -> str:
        command = kwargs["command"]
        session_id = kwargs.get("session_id") or "default"
        reset_session = bool(kwargs.get("reset_session"))
        unsafe_reason = self._unsafe_command_reason(command)
        if unsafe_reason:
            return f"[session={session_id}] Tool terminal.run failed: blocked unsafe command: {unsafe_reason}"

        if reset_session:
            self.memory.reset_terminal_session(session_id)

        state = self.memory.load_terminal_session(session_id)
        cwd = self._resolve_cwd(kwargs.get("cwd"), state)
        state.cwd = str(cwd)

        builtin_result = self._handle_cd(command, cwd, state)
        state.history.append(command)
        state.history = state.history[-20:]
        if builtin_result is not None:
            self.memory.save_terminal_session(state)
            return f"[session={session_id}] {builtin_result}"

        completed = self._run_shell(command, state.cwd)
        output = completed.stdout.strip() or completed.stderr.strip() or "Command completed with no output."
        state.history.append(f"exit={completed.returncode}")
        state.history = state.history[-20:]
        self.memory.save_terminal_session(state)
        return f"[session={session_id}] cwd={state.cwd} exit={completed.returncode}\n{output[:4000]}"


class TerminalResetTool(Tool):
    name = "terminal.reset"
    description = "Reset a terminal session and clear its persisted working directory."
    input_schema = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"}
        }
    }

    def __init__(self) -> None:
        self.memory = MemoryStore()

    def run(self, **kwargs: str) -> str:
        session_id = kwargs.get("session_id") or "default"
        self.memory.reset_terminal_session(session_id)
        return f"Reset terminal session: {session_id}"
