from __future__ import annotations

import json
from typing import Any

from app.schemas import ChatAttachment, ToolCallRecord
from app.services.model_client import ModelClient
from app.tools.registry import ToolRegistry


class LLMAgent:
    name = "base-agent"
    system_prompt = "You are a helpful agent."

    def __init__(self) -> None:
        self.model_client = ModelClient()

    def build_messages(
        self,
        message: str,
        attachments: list[ChatAttachment],
        memory_summary: str,
    ) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    f"Memory summary: {memory_summary}\n"
                    f"User request: {message}"
                ),
            }
        ]
        inline_images = 0
        path_only_images = 0
        for attachment in attachments:
            if attachment.kind != "image":
                continue
            if attachment.data_url:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": attachment.data_url},
                    }
                )
                inline_images += 1
            elif attachment.path:
                path_only_images += 1

        if inline_images == 0 or path_only_images > 0:
            content[0]["text"] += (
                f"\nImage attachments summary: inline_images={inline_images}, path_only_images={path_only_images}."
            )

        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": content},
        ]

    async def handle(
        self,
        message: str,
        registry: ToolRegistry,
        attachments: list[ChatAttachment],
        memory_summary: str,
        session_id: str,
    ) -> tuple[str, list[ToolCallRecord]]:
        messages = self.build_messages(message, attachments, memory_summary)
        tool_calls: list[ToolCallRecord] = []

        try:
            for _ in range(4):
                response = await self.model_client.chat(messages, tools=registry.specs())
                assistant_message = self.model_client.extract_message(response)
                raw_tool_calls = assistant_message.get("tool_calls") or []

                if raw_tool_calls:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": assistant_message.get("content") or "",
                            "tool_calls": raw_tool_calls,
                        }
                    )
                    for tool_call in raw_tool_calls:
                        name = tool_call["function"]["name"]
                        raw_args = tool_call["function"].get("arguments") or "{}"
                        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                        if name.startswith("terminal."):
                            args.setdefault("session_id", session_id)
                        try:
                            result = await registry.arun(name, args)
                        except Exception as exc:
                            result = f"Tool {name} failed: {exc}"
                        tool_calls.append(ToolCallRecord(name=name, args=args, result=result))
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call["id"],
                                "name": name,
                                "content": result,
                            }
                        )
                    continue

                reply = self.model_client.extract_text(response).strip()
                if reply:
                    return reply, tool_calls
        except ValueError as exc:
            if tool_calls:
                return f"我完成了部分操作，但当前配置不支持这次图像输入：{exc}", tool_calls
            return f"当前配置不支持这次图像输入：{exc}", tool_calls
        except Exception as exc:
            if tool_calls:
                return f"我完成了部分操作，但模型服务在生成最终回复时出错：{exc}", tool_calls
            return f"模型服务暂时不可用，我先保留任务上下文。错误信息：{exc}", tool_calls

        return "我已经完成分析，但当前模型没有给出最终自然语言回复。", tool_calls
