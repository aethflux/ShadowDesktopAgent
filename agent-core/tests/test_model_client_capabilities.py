from __future__ import annotations

import pytest

from app.config import settings
from app.services.model_client import ModelClient


def test_minimax_text_model_does_not_support_vision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "provider", "minimax")
    monkeypatch.setattr(settings, "minimax_model", "MiniMax-Text-01")

    client = ModelClient()

    assert client.supports_vision() is False


def test_openai_gpt4o_model_supports_vision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "provider", "openai")
    monkeypatch.setattr(settings, "openai_model", "gpt-4o-mini")

    client = ModelClient()

    assert client.supports_vision() is True


@pytest.mark.asyncio
async def test_text_only_model_rejects_image_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "provider", "minimax")
    monkeypatch.setattr(settings, "minimax_model", "MiniMax-Text-01")

    client = ModelClient()
    messages = [
        {"role": "system", "content": "You are Hoshino."},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look at this"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
        },
    ]

    with pytest.raises(ValueError, match="does not support image inputs"):
        await client.complete_structured_messages(messages)
