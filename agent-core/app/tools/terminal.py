from __future__ import annotations

import os
import subprocess
from pathlib import Path

from app.config import settings
from app.schemas import TerminalSessionState
from app.services.memory import MemoryStore
from app.tools.base import Tool


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
            return Path(requested_cwd).expanduser().resolve()
        return Path(state.cwd).expanduser().resolve()

    def _handle_cd(self, command: str, cwd: Path, state: TerminalSessionState) -> str | None:
        stripped = command.strip()
        lowered = stripped.lower()
        if lowered == "pwd":
            return str(cwd)
        if not (lowered.startswith("cd ") or lowered == "cd"):
            return None

        target = stripped[2:].strip().strip('"') if len(stripped) > 2 else ""
        target_path = Path.home() if not target else (cwd / target if not Path(target).is_absolute() else Path(target))
        target_path = target_path.expanduser().resolve()
        if not target_path.exists() or not target_path.is_dir():
            return f"Directory not found: {target_path}"
        state.cwd = str(target_path)
        return f"Changed directory to {target_path}"

    def run(self, **kwargs: str) -> str:
        command = kwargs["command"]
        session_id = kwargs.get("session_id") or "default"
        reset_session = bool(kwargs.get("reset_session"))

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

        completed = subprocess.run(
            command,
            cwd=state.cwd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=settings.command_timeout_seconds,
            env=os.environ.copy(),
        )
        output = completed.stdout.strip() or completed.stderr.strip() or "Command completed with no output."
        state.history.append(f"exit={completed.returncode}")
        state.history = state.history[-20:]
        self.memory.save_terminal_session(state)
        return f"[session={session_id}] cwd={state.cwd}\n{output[:4000]}"


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
