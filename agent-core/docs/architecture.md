# Architecture Notes

## Request Flow

1. 用户在 Electron 聊天面板输入文本、图片或语音
2. `desktop` 调用 `agent-core /api/chat`
3. `MultiAgentOrchestrator` 写入 memory，生成上下文
4. `RouterAgent` 执行启发式路由，并尝试通过模型生成结构化规划
5. `PlannerAgent` 融合规划结果
6. 目标 sub-agent 执行任务，并按需调用 `ToolRegistry`
7. 返回回复、调用轨迹、memory 摘要给桌面端

## Why This Design Matters

- 同时覆盖桌面端、Agent Core、工具系统和模型接入
- 具备 GUI Agent、Code Agent、Companion Agent 三类能力
- 体现了面向实际产品的接口设计与模块边界
