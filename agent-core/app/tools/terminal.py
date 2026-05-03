from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from app.config import settings
from app.schemas import TerminalSessionState
from app.services.memory import MemoryStore
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
        if requested_cwd:
            path = Path(requested_cwd).expanduser().resolve()
        else:
            path = Path(state.cwd).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            raise FileNotFoundError(f"cwd not found: {path}; current session cwd={state.cwd}")
        if not self._is_path_allowed(path):
            raise PermissionError(
                f"cwd outside allowed workspace: {path}; allowed root={settings.command_workspace_root.resolve()}"
            )
        return path

    @staticmethod
    def _is_relative_to(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _is_path_allowed(self, path: Path) -> bool:
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
