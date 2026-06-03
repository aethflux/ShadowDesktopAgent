# Shadow Desktop Agent

Shadow 是一个常驻桌面的多模态 Agent。项目由 FastAPI 后端和 Electron 桌面端组成，支持桌宠交互、文本对话、屏幕观察、主动陪聊、语音播报、文生图、语义记忆和受控工具调用。

## 核心能力

- 桌面端：透明桌宠、独立输入框、控制台、右键菜单和设置页。
- Agent 后端：意图路由、任务规划、主动陪伴（屏幕观察 + 主动话题）和工具执行。
- 多模型：文本、视觉、embedding、TTS、文生图可独立配置。
- 个性化：人设预设与立绘 1:1 绑定；可用 AI（ModelScope 文生图）生成自定义控制台场景与桌宠立绘。
- 记忆：JSONL 会话历史 + ChromaDB 语义检索。
- 工具：受控终端、外部 CLI、截图、MCP filesystem、本地 skills。
- 安全：限制工作目录、拦截高风险命令、过滤 MCP 写入类工具；不做桌面 GUI 自动化（无鼠标键盘模拟）。

## 技术栈

- Backend: Python, FastAPI, Pydantic, ChromaDB
- Desktop: Electron, TypeScript, HTML/CSS
- Models: MiniMax, ModelScope, OpenAI, Anthropic, vLLM
- Tooling: MCP stdio, pytest, ruff, GitHub Actions

## 项目结构

```text
agent-core/
├── app/          # FastAPI, agents, services, tools
├── eval/         # intent / collaboration eval
├── tests/        # pytest tests
├── skills/       # local prompt skills
├── memory/       # local memory and ChromaDB data
└── .env.example

desktop/
├── src/          # Electron main process and preload
└── renderer/     # pet, chat input, console UI
```

## 启动

### 1. 配置后端环境变量

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

API Key 只从 `.env` 读取，不通过控制台保存。

### 2. 启动后端

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

### 3. 启动桌面端

另开一个 PowerShell：

```powershell
cd desktop
npm install
npm start
```

桌面端默认连接 `http://127.0.0.1:8787`。

## 常用模型配置

推荐组合：

```env
PROVIDER=minimax
MINIMAX_MODEL=MiniMax-M2.7

VISION_PROVIDER=modelscope
VISION_MODEL=Qwen/Qwen3-VL-8B-Instruct

EMBEDDING_PROVIDER=modelscope
EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
```

离线测试或关闭语义记忆：

```env
EMBEDDING_PROVIDER=hash
ENABLE_SEMANTIC_MEMORY=false
```

## 语音

桌宠朗读（TTS）只用 Edge Neural TTS，失败时回退到浏览器本地语音合成：

```env
ENABLE_EDGE_TTS=true
EDGE_TTS_VOICE=zh-CN-XiaoxiaoNeural
EDGE_TTS_RATE=+0%
EDGE_TTS_PITCH=+0Hz
```

语音输入（ASR）是可选的 MiniMax 接口，默认关闭（`ENABLE_MINIMAX_VOICE`）。

## 生图（文生图）

接入 ModelScope 文生图，让项目自己定制控制台场景和桌宠立绘：

```env
ENABLE_IMAGE_GENERATION=true
IMAGE_MODEL=MusePublic/489_ckpt_FLUX_1     # 换成你的 ModelScope key 能访问的模型
```

- 复用 `MODELSCOPE_API_KEY`，走 ModelScope 异步文生图任务；生成图片存到 `artifacts/generated/`。
- 设置页「AI 生成」：输入提示词 → 生成 → 预览 → 一键应用为当前场景或桌宠立绘，含图库（保存 / 应用 / 删除）。
- 也暴露为 agent 工具 `image.generate`，可在对话里让桌宠按描述作画。

## 主动陪伴

开启「持续陪伴」后，桌宠不只是看屏幕——屏幕没变化时也会偶尔主动起个轻松话题，陪伴感更真实：

- 话题来源轮换：呼应你的**记忆 / 过往对话**、看**时间与节奏**（早晚问候、久坐提醒）、念一条**免费 RSS 新闻**。
- 频率克制（默认约 4–5 分钟最多一句），桌面端按系统真实空闲时间判断——你真离开键盘就自动安静。
- 新闻零成本：用公开 RSS/Atom 源（默认 Hacker News + 阮一峰），无需任何 API key；抓取失败自动退回记忆 / 时间话题。

```env
ENABLE_PROACTIVE_CHATTER=true
PROACTIVE_MIN_INTERVAL_SECONDS=270
NEWS_FEEDS=https://news.ycombinator.com/rss,https://www.ruanyifeng.com/blog/atom.xml
```

## 本地验证

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
