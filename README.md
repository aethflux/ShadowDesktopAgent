# Hoshino Desktop Digital Twin Agent

Hoshino 是一个桌面数字分身 / 桌宠 Agent 项目，围绕常驻桌面交互、多模态理解、工具调用和 Agent 编排展开。

## 核心定位

这个项目不是单纯聊天机器人，而是一个常驻桌面的数字分身：

- 能以桌宠形态停留在桌面上
- 能通过文本、图片和语音与用户交流
- 能持续观察屏幕，在合适的时候主动评论或提醒
- 能维护用户画像、偏好、目标和近期记忆
- 能调用工具，包括截图、终端、MCP/skills 扩展入口
- **支持 MiniMax / ModelScope / vLLM / OpenAI / Anthropic 五种 Provider 热切换，无需改代码**
- **主聊天模型和视觉模型可拆分配置：文本走 MiniMax，截图/图片理解走 ModelScope VL**
- **Anthropic Provider 启用 Prompt Caching，屏幕观察场景 token 成本降低 ~70%**
- **基于 chromadb + ModelScope / OpenAI / hash embeddings 实现语义记忆检索**
- **完整 MCP stdio JSON-RPC 实现，接入 filesystem server 真实调用**
- **66 个 pytest 单元测试，GitHub Actions CI，Intent 路由准确率 100%（31/31）**

## 架构图

```text
┌──────────────────────────────────────────────────────────────────────┐
│                       Desktop (Electron UI)                          │
│   桌宠窗口 (240×430) ◄──► 控制台 (1080×760) ◄──► 后端 HTTP API     │
└────────────────────────────┬─────────────────────────────────────────┘
                             │  POST /api/chat
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    FastAPI  MultiAgentOrchestrator                   │
│                                                                     │
│  ┌──────────────┐    ┌──────────────┐    ┌─────────────────────┐  │
│  │ RouterAgent  │───►│ PlannerAgent  │───►│  Execution Agents  │  │
│  │ local rules  │    │ intent fusion │    │  CompanionAgent    │  │
│  │ + LLM plan   │    │ threshold 0.72│    │  DesktopAgent       │  │
│  └──────────────┘    └──────────────┘    │  TerminalAgent      │  │
│                                           └─────────────────────┘  │
│  ┌────────────────┐  ┌────────────────┐  ┌─────────────────────┐   │
│  │ MemoryStore    │  │ VectorStore    │  │ ToolRegistry        │   │
│  │ JSONL session  │  │ (chromadb)    │  │ terminal/screen/    │   │
│  │ + JSON profile │  │ semantic recall│  │ GUI + MCP bridge   │   │
│  └────────────────┘  └────────────────┘  └─────────────────────┘   │
│                                              │                      │
│                            ┌─────────────────┘                      │
│                            ▼                                        │
│              ┌──────────────────────────────┐                       │
│              │  MCPClient (stdio JSON-RPC) │                       │
│              │  filesystem server @ skills/ │                       │
│              └──────────────────────────────┘                       │
└─────────────────────────────────────────────────────────────────────┘
                             │
         ┌───────────────────┼───────────────────┼───────────────────┐
         ▼                   ▼                   ▼                   ▼
   vLLM (local)        OpenAI (cloud)     Anthropic (cloud)    MiniMax (cloud)      ModelScope (cloud)
   Qwen2.5-VL          GPT-4o-mini        Claude Sonnet 4      MiniMax-M2.7         Qwen3-VL-8B
   Port 8000           api.openai.com     api.anthropic.com   api.minimax.chat/v1  api-inference.modelscope.cn
   Prompt Caching: ✗   Prompt Caching: auto  Prompt Caching: ✓  Prompt Caching: ✗   Prompt Caching: ✗
```

## 项目结构

```text
agent-core/
├── app/
│   ├── agents/          # RouterAgent, PlannerAgent, DesktopAgent,
│   │                    # CompanionAgent, TerminalAgent, LLMAgent
│   ├── services/        # model_client (multi-provider), memory,
│   │                    # vector_store (chromadb), embeddings, mcp_client,
│   │                    # context_manager, skill_loader
│   ├── tools/           # terminal, screen, GUI, mcp_tool (bridge)
│   ├── main.py          # FastAPI entry + startup/shutdown hooks
│   ├── config.py        # Settings (env-driven, all providers, absolute paths)
│   ├── schemas.py       # Pydantic models
│   └── orchestrator.py  # MultiAgentOrchestrator + bootstrap()
├── eval/                # 31-case intent routing eval + run script
├── tests/               # 66 unit tests (pytest, no network)
├── skills/              # local skill definitions (code-helper, english-tutor, etc.)
├── memory/              # session memory + chromadb semantic memory
├── artifacts/           # screenshots dir (served as /artifacts/)
├── docs/                # architecture notes
├── Dockerfile           # multi-stage, runtime image
├── docker-compose.yml   # cloud API mode (no GPU needed)
├── docker-compose.vllm.yml  # local vLLM override
└── .env.example         # all env vars documented

desktop/
├── src/main.ts          # Electron main process (IPC + BrowserWindow)
└── renderer/            # 桌宠 (pet.html/css/js) + 控制台 (panel.html)

.github/workflows/ci.yml # ruff lint + pytest + intent/collab eval gate
```

## 快速启动

### 方式一：云端 API（5 分钟跑起来，无需 GPU）

```bash
# 1. 配置密钥
cp agent-core/.env.example agent-core/.env
# 编辑 .env，设置 MINIMAX_API_KEY=... 和 MODELSCOPE_API_KEY=...

# 2. 启动后端（Prompt Caching 自动启用）
cd agent-core
docker compose up

# 3. 启动桌面端（另开一个终端）
cd desktop && npm install && npm start
```

### 方式二：本地 vLLM（需要 GPU + vLLM 服务）

```bash
# vLLM 服务运行在 localhost:8000
cd agent-core
docker compose -f docker-compose.yml -f docker-compose.vllm.yml up
```

### 方式三：纯 Python（开发调试）

```bash
cd agent-core
python -m venv .venv
source .venv/Scripts/activate      # Linux/macOS
# .venv\Scripts\Activate.ps1       # Windows PowerShell
pip install -e .
pip install pytest pytest-asyncio ruff chromadb  # dev deps

# 运行测试和 eval
pytest tests/ -q
python -m eval.run_intent_eval --min-accuracy 0.80
ruff check app/

# 启动服务
uvicorn app.main:app --reload --port 8787
```

## Provider 切换（无需改代码）

只需修改环境变量：

| Provider | `PROVIDER` | 备注 |
| --- | --- | --- |
| Anthropic（推荐） | `anthropic` | Prompt Caching + cache_control，token 成本 ~70%↓ |
| OpenAI | `openai` | 自动缓存稳定前缀，性价比高 |
| 本地 vLLM | `vllm` | 需要 GPU，支持多模态（Qwen2.5-VL） |
| MiniMax（推荐中国用户） | `minimax` | Token Plan 默认可用，适合编程与 Agent 场景，推荐 `MiniMax-M2.7` |
| ModelScope | `modelscope` | OpenAI-compatible API，可作为视觉或文本 Provider |

```env
# 云端 Anthropic（Prompt Caching 降低 token 成本）
PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-xxxxx
ANTHROPIC_MODEL=claude-sonnet-4-20250514
ENABLE_PROMPT_CACHE=true

# MiniMax（推荐中国用户，无需科学上网）
PROVIDER=minimax
MINIMAX_API_KEY=EMG-xxxxxxxxxxxxxxxx
MINIMAX_MODEL=MiniMax-M2.7
MINIMAX_API_BASE=https://api.minimax.chat/v1

# 视觉模型：主聊天可继续用 MiniMax，截图/图片理解独立走 ModelScope
VISION_PROVIDER=modelscope
VISION_MODEL=Qwen/Qwen3-VL-8B-Instruct
MODELSCOPE_MODEL=Qwen/Qwen3-VL-8B-Instruct
EMBEDDING_PROVIDER=modelscope
EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
MODELSCOPE_API_KEY=ms-xxxxxxxxxxxxxxxx
MODELSCOPE_API_BASE=https://api-inference.modelscope.cn/v1

# 语音播报：默认 Edge Neural TTS，失败时回退到浏览器语音
ENABLE_EDGE_TTS=true
EDGE_TTS_VOICE=zh-CN-XiaoxiaoNeural
EDGE_TTS_RATE=+0%
EDGE_TTS_PITCH=+0Hz

# ModelScope CosyVoice2 Studio 可选；确认能返回非静音音频后再启用
ENABLE_MODELSCOPE_TTS=false
MODELSCOPE_TTS_API_BASE=https://iic-cosyvoice2-0-5b.ms.show
MODELSCOPE_TTS_MODEL=iic/CosyVoice2-0.5B
MODELSCOPE_TTS_MODE=自然语言控制
MODELSCOPE_TTS_INSTRUCTION=用温柔自然的中文女声朗读

# 云端 OpenAI
PROVIDER=openai
OPENAI_API_KEY=sk-xxxxx
OPENAI_MODEL=gpt-4o-mini

# 本地 vLLM
PROVIDER=vllm
VLLM_API_BASE=http://127.0.0.1:8000/v1
VLLM_API_KEY=EMPTY
VLLM_MODEL=Qwen/Qwen2.5-VL-7B-Instruct

# 直接使用 ModelScope 文本/视觉模型
PROVIDER=modelscope
MODELSCOPE_API_KEY=ms-xxxxxxxxxxxxxxxx
MODELSCOPE_MODEL=Qwen/Qwen3-VL-8B-Instruct
```

## 技术亮点详解

### 多 Provider 模型客户端
`app/services/model_client.py` 统一封装了 MiniMax / ModelScope / vLLM / OpenAI / Anthropic 五种调用路径：
- Anthropic 分支：消息格式翻译（OpenAI style → Anthropic style）、`cache_control` 注入、`stop_reason` → `finish_reason` 回填
- OpenAI-compatible 分支：复用 MiniMax、ModelScope、OpenAI、vLLM 的 `/chat/completions` 形态
- 视觉分支：`VISION_PROVIDER` / `VISION_MODEL` 可独立于主聊天模型配置
- 所有分支返回统一的 OpenAI shape，agent 代码无需感知 provider 差异

### 屏幕与图片视觉
`DesktopAgent` 在处理截图和用户上传图片时使用独立视觉客户端：

- 默认 `VISION_PROVIDER=modelscope`
- 默认 `VISION_MODEL=Qwen/Qwen3-VL-8B-Instruct`
- 主聊天仍可使用 `PROVIDER=minimax` + `MINIMAX_MODEL=MiniMax-M2.7`
- 如果文本和视觉必须统一，也可以设置 `PROVIDER=modelscope` 并复用同一个 `MODELSCOPE_MODEL`

### Prompt Caching（Anthropic）
在 `enable_prompt_cache=true` 时，system prompt 末尾附加 `cache_control: {type: "ephemeral"}`，Anthropic 将之前所有 token 写入缓存盘，后续请求只需传输增量。屏幕持续观察等高频相同上下文的场景受益最大。

### 语义记忆（chromadb + ModelScope/OpenAI/hash embeddings）
`app/services/vector_store.py` + `app/services/embeddings.py`：
- `EMBEDDING_PROVIDER=modelscope`：调用 ModelScope API-Inference `Qwen/Qwen3-Embedding-0.6B`
- `EMBEDDING_PROVIDER=openai`：调用 OpenAI `text-embedding-3-small`
- `EMBEDDING_PROVIDER=hash`：零依赖离线模式，MD5 n-gram 哈希 + L2 归一化，向量余弦相似度用于 CI 测试
- 每条对话写入 chroma collection（session 级别隔离），检索时取 top-k 最相关记忆拼入 prompt

### MCP 完整实现
`app/services/mcp_client.py`：
- stdio JSON-RPC 2.0 协议（`notifications/initialized`、id-matching 响应分拣）
- `MCPServerProcess` 生命周期管理（start / list_tools / call_tool / stop）
- `ToolRegistry.load_mcp_tools()` 在 startup 时发现并注册所有 MCP 工具
- `MCPBridgeTool` 将远程工具适配为本地 `Tool` 接口，LLM 无感调用

### Intent 路由 Eval
`eval/run_intent_eval.py`：离线评测 RouterAgent 本地分类器，31 条测试集，**准确率 100%**：
```
Intent router eval — 31 cases
  intent accuracy   : 31/31 = 100.0%
  delegate accuracy : 31/31 = 100.0%
```
GitHub Actions 在每次 PR 运行 ruff → pytest → eval，准确率门槛 `--min-accuracy 0.80`。

### 语音

桌面端默认调用后端 Edge Neural TTS，并保留浏览器 Web Speech API 作为兜底：

- **语音输入**：前端直接调用浏览器 `SpeechRecognition`
- **语音播报**：后端 `/api/voice/tts` 默认使用 `edge` 生成音频文件，桌面端直接播放
- **音色切换**：桌宠窗口内可切换清亮女声、甜美女声、青年男声、旁白男声，对应 Edge Neural TTS 中文音色
- **形象切换**：桌宠窗口内可切换虚拟主播、见习剑士、电子搭档三套 CSS 形象
- **ModelScope TTS**：启用 `ENABLE_MODELSCOPE_TTS=true` 后调用 `iic/CosyVoice2-0.5B` Studio；如果返回静音或请求失败，会回退到浏览器语音
- **备选接入**：MiniMax / Gemini TTS 代码仍保留为可选分支，默认关闭
- **兼容策略**：如果当前环境不支持 Web Speech API，界面会提示改用文本输入

### 主动陪伴策略引擎
`app/services/companion_strategy.py` — 专注力检测 + 情绪感知 + 智能提醒：

**参与度分数**（0–1）：键盘速率（归一化到 0–120 kpm）+ 鼠标速率 + 上次交互距今时间

**三种策略决策**：

| 状态 | 条件 | Hoshino 行为 |
| --- | --- | --- |
| 用户离开（idle） | `is_idle=True` | 跳过截图，节省 API 调用 |
| 持续低迷 | 参与度 < 0.30 连续 3 次，间隔 > 3 min | 温柔鼓励 + 起身提醒 |
| 心流状态 | 参与度 > 0.72 连续 3 次，间隔 > 5 min | 简短鼓励后安静退出，不打断 |
| 正常 | 其他 | 正常屏幕观察，不主动发言 |

情绪分类（关键字驱动，无依赖）：用户消息含 "棒/成功/完成" → positive，"累/烦/不懂" → negative，调整 nudge 语气。

桌面端通过 `_engagementState()` 追踪 keypresses / mouse_moves / idle，混入每次 `observe()` 请求。

### 多 Agent 协作评测

`eval/run_collaboration_eval.py` + `eval/collaboration_scenarios.json`：

- 10 个协作场景，覆盖路由正确性、工具调用、记忆连续性、Skill 触发、MCP 发现
- 支持 `--offline`（CI 默认，无网络调用）和在线两种模式
- 当前离线准确率：**15/15 = 100%**

### Skill 动态加载系统

`app/services/skill_loader.py` + `skills/` 目录：

```
skills/
  general-assistant/skill.md   # YAML frontmatter + 行为 prompt
  code-helper/skill.md         # 触发词: 代码/bug/实现/修复…
  english-tutor/skill.md        # 触发词: 翻译/英语/词汇/口语…
  github-review/skill.md        # 触发词: review/diff/PR/审查…
```

**目录格式**（`skill.md`）：

```yaml
---
name: code-helper
description: 专注于代码实现、bug 修复、重构和优化。
triggers:
  - 代码
  - bug
  - 修复
  - 重构
  - 写个
---
# System Prompt

你是一个专注于代码质量和工程实践的编程助手。
当用户询问代码时…
```

**注入机制**：`ContextManager.build_prompt_context` 在构建 prompt 时，先用用户消息匹配 skill triggers，命中的 skill prompt 片段被 prepend 到 system prompt 里，agent 无需额外工具调用即可获得领域特定指导。

### 工程指标

| 指标 | 数值 |
| --- | --- |
| Python 代码行数（不含 venv） | ~1700 |
| pytest 测试用例 | 66 |
| Intent 路由准确率 | 100% (31/31) |
| 协作评测 | 100% (15/15) |
| CI 覆盖 | ruff lint + pytest + intent eval + collab eval |
| 支持的 LLM Provider | MiniMax / ModelScope / Anthropic / OpenAI / vLLM |
| 支持的 Embedding Provider | ModelScope / OpenAI / Hash（零依赖） |
| Docker 镜像 | multi-stage, ~800 MB |
