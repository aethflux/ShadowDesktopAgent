from __future__ import annotations

from app.agents.llm_agent import LLMAgent


class CompanionAgent(LLMAgent):
    """Default chat agent. The system prompt is now produced by
    :class:`PersonaBuilder` (see ``LLMAgent.get_system_prompt``) so the user
    can swap personas from the settings UI without touching this file."""

    name = "companion-agent"
    allowed_tool_names = frozenset({
        "image.generate",
        "skill.list",
        "mcp.servers",
        "mcp.list_tools",
    })
