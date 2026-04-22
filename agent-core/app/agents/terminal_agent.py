from __future__ import annotations

from app.agents.llm_agent import LLMAgent


class TerminalAgent(LLMAgent):
    name = "terminal-agent"
    system_prompt = (
        "You are Hoshino's terminal and coding specialist. "
        "Use terminal.run when shell execution is useful, explain results briefly, and avoid unnecessary commands."
    )
