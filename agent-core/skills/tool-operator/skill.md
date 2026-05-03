---
name: tool-operator
description: Guides safe use of external CLI, MCP servers, and managed prompt skills.
triggers:
  - cli
  - mcp
  - skill
  - tool
  - 外部cli
  - 外部mcp
  - 外部skill
  - 工具
  - 执行能力
---

When a request involves external tools, first inspect the available capability surface.

Operating rules:
- Prefer `cli.run` for direct external executable checks because it avoids shell parsing.
- Prefer `mcp.servers` and `mcp.list_tools` before calling bridged `mcp.*.*` tools.
- Prefer `skill.list`, `skill.create`, and `skill.install_from_url` for prompt-skill management.
- Treat downloaded skills as prompt text only. Do not execute downloaded code.
- If a command is blocked by safety rules, explain the boundary and propose a read-only or scoped alternative.
