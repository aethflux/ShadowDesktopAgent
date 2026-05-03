from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.config import settings
from app.tools.base import Tool
from app.tools.terminal import unsafe_command_reason


class ExternalCLITool(Tool):
    name = "cli.run"
    description = (
        "Run an allowlisted external CLI without a shell. Use this for tools like "
        "git, node, npm run, npx --version, or python -m pytest when direct CLI "
        "execution is safer than shell text."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Executable name, e.g. git, node, npm, python."},
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "CLI arguments. Do not include shell operators.",
            },
            "cwd": {"type": "string"},
            "timeout_seconds": {"type": "integer", "minimum": 1},
        },
        "required": ["command"],
    }

    def _allowed_commands(self) -> set[str]:
        return {
            item.strip().lower()
            for item in settings.external_cli_allowlist.split(",")
            if item.strip()
        }

    @staticmethod
    def _resolve_cwd(requested_cwd: str | None) -> Path:
        root = settings.command_workspace_root.expanduser().resolve()
        path = Path(requested_cwd).expanduser().resolve() if requested_cwd else root
        if not path.exists() or not path.is_dir():
            raise FileNotFoundError(f"cwd not found: {path}")
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise PermissionError(f"cwd outside allowed workspace: {path}; allowed root={root}") from exc
        return path

    @staticmethod
    def _normalize_args(raw_args: Any) -> list[str]:
        if raw_args is None:
            return []
        if not isinstance(raw_args, list):
            raise ValueError("args must be a list of strings")
        return [str(arg) for arg in raw_args]

    def run(self, **kwargs: Any) -> str:
        command = str(kwargs["command"]).strip()
        args = self._normalize_args(kwargs.get("args"))
        if not command:
            return "Tool cli.run failed: empty command"
        if any(separator in command for separator in ("/", "\\", ":", " ", "\t", "\n")):
            return "Tool cli.run failed: command must be an executable name, not a path or shell snippet"

        lowered = command.lower()
        if lowered not in self._allowed_commands():
            return (
                f"Tool cli.run blocked: command '{command}' is not allowlisted. "
                f"Allowed: {', '.join(sorted(self._allowed_commands()))}"
            )

        flat_command = " ".join([command, *args])
        unsafe_reason = unsafe_command_reason(flat_command)
        if unsafe_reason:
            return f"Tool cli.run failed: blocked unsafe command: {unsafe_reason}"

        cwd = self._resolve_cwd(kwargs.get("cwd"))
        executable = shutil.which(command)
        if not executable:
            return f"Tool cli.run failed: executable not found on PATH: {command}"

        requested_timeout = int(kwargs.get("timeout_seconds") or settings.command_timeout_seconds)
        timeout = max(1, min(requested_timeout, settings.command_timeout_seconds))
        completed = subprocess.run(
            [executable, *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
        )
        output = completed.stdout.strip() or completed.stderr.strip() or "Command completed with no output."
        return f"[cli={command}] cwd={cwd} exit={completed.returncode}\n{output[:4000]}"
