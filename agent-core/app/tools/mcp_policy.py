from __future__ import annotations

from app.config import settings


def _csv_items(value: str) -> set[str]:
    return {item.strip().lower() for item in value.split(",") if item.strip()}


def is_mcp_tool_allowed(tool_name: str) -> bool:
    lowered = tool_name.strip().lower()
    if not lowered:
        return False
    if lowered in _csv_items(settings.mcp_tool_denylist):
        return False
    return not any(keyword in lowered for keyword in _csv_items(settings.mcp_tool_blocked_keywords))


def mcp_tool_policy_label(tool_name: str) -> str:
    return "available" if is_mcp_tool_allowed(tool_name) else "blocked by local bridge policy"
