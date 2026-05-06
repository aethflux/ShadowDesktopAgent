# Shadow Desktop Digital Twin Agent

Shadow 是一个常驻桌面的数字分身 / 桌宠 Agent 项目。它由 FastAPI 后端和 Electron 桌面端组成，支持对话、屏幕观察、语音播报、记忆、工具调用、受限终端、外部 CLI、MCP 和本地 skill 扩展。

## 当前验证状态

推荐启动路径是 **纯 Python 后端 + Electron 桌面端**。这是日常开发和本机调试使用的路径。

CI 会运行后端 lint、pytest、intent eval 和 collaboration eval。README 不再写固定测试数量、窗口尺寸或镜像大小，避免这些数字在代码变化后失真。

## 功能概览

- 桌宠窗口常驻桌面，可拖动、右键打开菜单和设置。
- 独立聊天输入框，可自由拖动，发送后优先进入桌宠对话链路。
- 控制台集中展示会话、任务步骤、工具调用、运行状态和设置。
- 持续陪伴使用固定 session，保留近期桌宠输出，避免同一屏幕内容反复重复。
- 支持文本、截图/图片理解、语音输入和 TTS 播报。
- 文本模型、视觉模型、embedding 和 TTS 可拆分配置。
- 支持 MiniMax、ModelScope、OpenAI、Anthropic 和本地 vLLM Provider。
- 支持 chromadb 语义记忆，embedding 可用 ModelScope / OpenAI / hash。
- 支持受控终端、allowlist 外部 CLI、MCP stdio 工具和本地 skills。
- 默认带有安全边界：限制命令目录、拦截高风险命令、限制 MCP 写入类工具。

## 项目结构

```text
agent-core/
├── app/
│   ├── agents/          # RouterAgent, PlannerAgent, CompanionAgent, DesktopAgent, TerminalAgent
│   ├── services/        # model client, memory, vector store, embeddings, MCP, voice, settings
│   ├── tools/           # terminal, cli, skill, MCP bridge, screen, permission tools
│   ├── main.py          # FastAPI app and API routes
│   ├── config.py        # env-driven settings
│   └── orchestrator.py  # multi-agent orchestration
├── eval/                # intent and collaboration eval
├── tests/               # pytest tests
├── skills/              # local prompt skills
├── memory/              # local memory and chromadb data
├── artifacts/           # screenshots and generated audio
└── .env.example

desktop/
├── src/main.ts          # Electron main process
└── renderer/            # pet, chat input, panel UI
```

## 快速启动

### 1. 配置环境变量

```powershell
Copy-Item agent-core\.env.example agent-core\.env
```

编辑 `agent-core\.env`，至少填写：

```env
PROVIDER=minimax
MINIMAX_API_KEY=你的 MiniMax API Key
MINIMAX_MODEL=MiniMax-M2.7

VISION_PROVIDER=modelscope
VISION_MODEL=Qwen/Qwen3-VL-8B-Instruct
MODELSCOPE_API_KEY=你的 ModelScope API Key

EMBEDDING_PROVIDER=modelscope
EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
```

API Key 目前只从 `.env` 读取，不在控制台里填写或保存。控制台负责切换 Provider、模型、音色、人格和功能开关。

### 2. 启动后端

Windows PowerShell：

```powershell
cd agent-core
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
pip install pytest pytest-asyncio ruff chromadb
uvicorn app.main:app --reload --host 127.0.0.1 --port 8787
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8787/health
Invoke-RestMethod http://127.0.0.1:8787/api/ready
```

如果提示 `127.0.0.1:8787` 已被占用，说明后端可能已经在运行，不要重复启动同一个端口。

### 3. 启动桌面端

另开一个 PowerShell：

```powershell
cd desktop
npm install
npm start
```

桌面端默认连接 `http://127.0.0.1:8787`。

## 模型配置

### 推荐组合

```env
PROVIDER=minimax
MINIMAX_MODEL=MiniMax-M2.7

VISION_PROVIDER=modelscope
VISION_MODEL=Qwen/Qwen3-VL-8B-Instruct

EMBEDDING_PROVIDER=modelscope
EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
```

如果希望文本和视觉统一走 ModelScope：

```env
PROVIDER=modelscope
MODELSCOPE_MODEL=Qwen/Qwen3-VL-8B-Instruct
VISION_PROVIDER=modelscope
VISION_MODEL=Qwen/Qwen3-VL-8B-Instruct
```

如果只想离线跑测试或不使用语义记忆：

```env
EMBEDDING_PROVIDER=hash
ENABLE_SEMANTIC_MEMORY=false
```

### 支持的 Provider

| 用途 | Provider |
| --- | --- |
| 文本对话 | MiniMax / ModelScope / OpenAI / Anthropic / vLLM |
| 视觉理解 | ModelScope / OpenAI-compatible / vLLM |
| Embedding | ModelScope / OpenAI / hash |
| TTS | Edge Neural TTS / ModelScope / Gemini / MiniMax / 浏览器语音兜底 |

## 语音

默认语音播报链路：

- 前端把要播报的文本发到 `/api/voice/tts`。
- 后端默认使用 Edge Neural TTS 生成音频。
- 如果后端 TTS 不可用，前端可回退到浏览器 Web Speech API。
- 朗读前会清理 Markdown、代码块、URL 和标点，避免把符号读出来。

常用配置：

```env
ENABLE_EDGE_TTS=true
EDGE_TTS_VOICE=zh-CN-XiaoxiaoNeural
EDGE_TTS_RATE=+0%
EDGE_TTS_PITCH=+0Hz

ENABLE_MODELSCOPE_TTS=false
MODELSCOPE_TTS_API_BASE=https://iic-cosyvoice2-0-5b.ms.show
MODELSCOPE_TTS_MODEL=iic/CosyVoice2-0.5B
MODELSCOPE_TTS_INSTRUCTION=用温柔自然的中文女声朗读
```

MiniMax 和 ModelScope TTS 都是可选分支。没有相应额度时保持关闭即可。

## 工具与安全边界

`terminal.run` 默认只能在 `COMMAND_WORKSPACE_ROOT` 下执行，超出目录会被拒绝。命令层会拦截删除、强制 Git 回滚、系统磁盘/电源/权限修改、shell 重定向、包安装/卸载等高风险操作。

`cli.run` 只能调用 `EXTERNAL_CLI_ALLOWLIST` 里的命令，不接受任意 shell 片段。

`skill.create` 和 `skill.install_from_url` 只能写入 `SKILLS_DIR` 下的 `skill.md`。下载的 skill 会被当作 prompt 文本，不会作为代码执行。

MCP 默认只暴露受限目录，并过滤写入、编辑、删除、移动、创建类工具。需要扩大能力时，优先缩小 MCP server 的目录范围，再调整 allowlist / denylist。

GUI 自动化默认关闭。只有显式设置 `ENABLE_GUI_AUTOMATION=true` 后，模型才可能调用鼠标键盘工具。

## 本地验证命令

后端：

```powershell
cd agent-core
ruff check app/
pytest tests/ -q
python -m eval.run_intent_eval --min-accuracy 0.80
python -m eval.run_collaboration_eval --offline --min-accuracy 0.80
```

桌面端：

```powershell
cd desktop
npm run build
```
