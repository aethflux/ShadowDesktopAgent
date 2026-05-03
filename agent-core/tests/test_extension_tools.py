from __future__ import annotations

import shutil
from pathlib import Path

from app.config import settings
from app.services.mcp_client import MCPClient
from app.services.skill_loader import SkillLoader
from app.tools.cli import ExternalCLITool
from app.tools.mcp_admin import MCPServerListTool
from app.tools.mcp_policy import is_mcp_tool_allowed, mcp_tool_policy_label
from app.tools.registry import ToolRegistry
from app.tools.skills import SkillCreateTool, SkillListTool


def _python_command() -> str:
    for command in ("python", "python3", "py"):
        if shutil.which(command):
            return command
    raise AssertionError("no python executable found on PATH")


def test_cli_run_invokes_allowed_external_cli() -> None:
    tool = ExternalCLITool()

    result = tool.run(command=_python_command(), args=["--version"], cwd=str(Path.cwd()))

    assert "exit=0" in result
    assert "Python" in result


def test_cli_run_blocks_package_mutation() -> None:
    tool = ExternalCLITool()

    result = tool.run(command=_python_command(), args=["-m", "pip", "install", "unsafe-package"])

    assert "blocked unsafe command" in result
    assert "python package mutation commands are blocked" in result


def test_cli_run_rejects_disallowed_executable() -> None:
    tool = ExternalCLITool()

    result = tool.run(command="cmd", args=["/c", "echo", "nope"])

    assert "not allowlisted" in result


def test_skill_create_and_list() -> None:
    create = SkillCreateTool()

    result = create.run(
        name="External Tool Skill",
        description="test skill",
        triggers=["external-tool-test"],
        prompt="Use the safest available external tool.",
    )

    assert "Created skill 'external-tool-skill'" in result
    assert (settings.skills_dir / "external-tool-skill" / "skill.md").exists()

    listing = SkillListTool().run()
    assert "external-tool-skill" in listing
    assert "external-tool-test" in listing


def test_new_skill_is_visible_after_loader_reload() -> None:
    SkillCreateTool().run(
        name="Reloaded Skill",
        description="reload test",
        triggers=["reload-trigger"],
        prompt="Reloaded prompt.",
    )
    loader = SkillLoader(settings.skills_dir)

    assert loader.match("please use reload-trigger")


def test_mcp_server_list_tool_reports_registered_server() -> None:
    client = MCPClient()
    client.register_server("demo", "npx", ["-y", "@modelcontextprotocol/server-filesystem", "."])

    result = MCPServerListTool(client).run()

    assert "demo" in result
    assert "server-filesystem" in result


def test_mcp_policy_blocks_write_like_tools() -> None:
    assert is_mcp_tool_allowed("read_text_file")
    assert not is_mcp_tool_allowed("write_file")
    assert not is_mcp_tool_allowed("edit_file")
    assert mcp_tool_policy_label("move_file") == "blocked by local bridge policy"


def test_tool_registry_exposes_extension_tools() -> None:
    registry = ToolRegistry(MCPClient())

    assert "cli.run" in registry.names()
    assert "skill.create" in registry.names()
    assert "skill.install_from_url" in registry.names()
    assert "mcp.servers" in registry.names()
    assert "mcp.list_tools" in registry.names()
