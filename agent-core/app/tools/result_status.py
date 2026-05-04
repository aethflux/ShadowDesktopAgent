from __future__ import annotations

import re
from typing import Literal

ToolStatus = Literal["completed", "failed", "blocked"]


def infer_tool_status(result: str, *, success: bool = True) -> ToolStatus:
    """Normalize raw tool output into a UI-facing status.

    Many tools return a text payload rather than raising. This helper catches
    those textual failure markers so the task panel does not label errors as
    completed.
    """
    lowered = result.lower()

    if (
        "blocked unsafe command" in lowered
        or lowered.startswith("tool ") and " blocked:" in lowered
        or "not allowlisted" in lowered
    ):
        return "blocked"

    if (
        not success
        or "failed:" in lowered
        or lowered.startswith("mcp tool error:")
        or "access denied" in lowered
        or "is not registered" in lowered
    ):
        return "failed"

    for match in re.finditer(r"\bexit=(\d+)\b", lowered):
        if int(match.group(1)) != 0:
            return "failed"

    return "completed"
