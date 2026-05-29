"""Regression tests for image-attachment fallback.

Bug: a message carrying an image could be routed (by a strong tool keyword)
to companion-agent or terminal-agent, which lack a vision path. The image
was fed to the text-only main model, producing a "does not support image
inputs" error instead of an answer.

Fix: image handling lives on the LLMAgent base class. When an agent's chat
model can't see, it falls back to the dedicated vision client. These tests
lock that in for the two non-desktop agents.
"""
from __future__ import annotations

import pytest

from app.agents.companion import CompanionAgent
from app.agents.terminal_agent import TerminalAgent
from app.schemas import ChatAttachment


def _vision_response(text: str) -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 4},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_cls", [CompanionAgent, TerminalAgent])
async def test_image_falls_back_to_vision_when_main_model_text_only(agent_cls, monkeypatch) -> None:
    agent = agent_cls()

    # Main chat model is text-only; vision client can see.
    monkeypatch.setattr(agent.model_client, "supports_vision", lambda: False)
    monkeypatch.setattr(agent.vision_client, "supports_vision", lambda: True)

    async def fake_vision_chat(messages, tools=None, **kwargs):
        return _vision_response("我看到一张猫的照片")

    monkeypatch.setattr(agent.vision_client, "chat", fake_vision_chat)

    # The text-only main model must never be invoked for an image-only turn.
    async def boom(*args, **kwargs):
        raise AssertionError("main chat model should not be called on image fallback")

    monkeypatch.setattr(agent.model_client, "chat", boom)

    attachment = ChatAttachment(kind="image", data_url="data:image/png;base64,AAAA")
    reply, tool_calls = await agent.handle(
        message="这是什么",
        registry=None,  # fallback returns before touching the registry
        attachments=[attachment],
        memory_summary="",
        session_id="img-test",
    )

    assert "猫" in reply
    assert tool_calls == []


@pytest.mark.asyncio
async def test_image_without_vision_gives_clear_message(monkeypatch) -> None:
    """If neither the chat model nor the vision client can see, the user gets
    a clear config hint — not a raw exception."""
    agent = CompanionAgent()
    monkeypatch.setattr(agent.model_client, "supports_vision", lambda: False)
    monkeypatch.setattr(agent.vision_client, "supports_vision", lambda: False)

    attachment = ChatAttachment(kind="image", data_url="data:image/png;base64,AAAA")
    reply, tool_calls = await agent.handle(
        message="看看这个",
        registry=None,
        attachments=[attachment],
        memory_summary="",
        session_id="img-test-2",
    )

    assert "视觉模型" in reply  # the _vision_unavailable_reply hint
    assert tool_calls == []
