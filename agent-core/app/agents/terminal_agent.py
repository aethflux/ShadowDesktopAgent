from __future__ import annotations

from app.agents.llm_agent import LLMAgent


class TerminalAgent(LLMAgent):
    """Terminal/coding specialist. Persona body comes from
    :class:`PersonaBuilder`; the role-specific terminal guidance (PowerShell,
    cli.run preference, destructive-command refusal) lives in the
    ``terminal-agent`` role addendum inside ``services/persona.py`` and is
    appended automatically by ``LLMAgent.get_system_prompt``.

    The ``get_system_prompt`` override below adds the few extras the
    role addendum doesn't cover (the runtime cwd hints) so they stay
    rooted in code rather than in user-editable persona text."""

    name = "terminal-agent"
    allowed_tool_names = frozenset({
        "terminal.run",
        "terminal.reset",
        "cli.run",
        "skill.list",
        "skill.create",
        "skill.install_from_url",
        "mcp.servers",
        "mcp.list_tools",
    })
    allowed_tool_prefixes = ("mcp.",)

    def get_system_prompt(self) -> str:
        base = super().get_system_prompt()
        # Operational knobs that should not be edited from the persona UI:
        # tool preferences and Windows shell quirks. We append rather than
        # replace so the user's tone choices still apply.
        return (
            f"{base} "
            "工具偏好：优先使用 cli.run；shell 命令使用 PowerShell 兼容语法（不要用 `dir /b`，用 `Get-ChildItem`）。"
            "可用 skill.list / skill.create / skill.install_from_url 管理 prompt skills，"
            "用 mcp.servers / mcp.list_tools 检查外部 MCP。"
            "不要凭空编造 cwd，使用用户消息上下文里的 backend 当前目录、项目根目录、agent-core 目录或 desktop 目录。"
        )
