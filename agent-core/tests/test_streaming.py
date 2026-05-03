"""Tests for the SSE streaming chat endpoint and the orchestrator's stream_chat."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.orchestrator import _chunk_text


def test_chunk_text_basic() -> None:
    assert _chunk_text("abcdefg", 3) == ["abc", "def", "g"]


def test_chunk_text_empty() -> None:
    assert _chunk_text("", 3) == []


def test_chunk_text_smaller_than_size() -> None:
    assert _chunk_text("hi", 50) == ["hi"]


def test_chunk_text_zero_size_returns_whole() -> None:
    assert _chunk_text("hello", 0) == ["hello"]


async def _fake_chat(self, messages, tools=None, tool_choice="auto", temperature=0.2):
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": "你好世界，这是一段流式分块的测试回复。"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 8},
    }


async def _fake_plan(self, message, tools, local_intent):
    return None  # planner falls back to local intent


@pytest.fixture
def streaming_client():
    """A TestClient with the LLM and planner mocked so the stream is offline."""
    from app.main import app

    with patch("app.services.model_client.ModelClient.chat", new=_fake_chat), \
         patch("app.agents.router.RouterAgent.plan", new=_fake_plan):
        with TestClient(app) as client:
            yield client


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    """Split an SSE response into (event_name, parsed_data) tuples."""
    events: list[tuple[str, dict]] = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event_name = None
        data_text = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_text = line[5:].strip()
        if event_name is None:
            continue
        try:
            payload = json.loads(data_text) if data_text else {}
        except json.JSONDecodeError:
            payload = {"_raw": data_text}
        events.append((event_name, payload))
    return events


def test_stream_chat_emits_full_event_sequence(streaming_client) -> None:
    with streaming_client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "你好", "session_id": "stream-test"},
    ) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = "".join(chunk for chunk in resp.iter_text())

    events = _parse_sse(body)
    names = [name for name, _ in events]

    assert names[0] == "start"
    assert "intent" in names
    assert "delta" in names
    assert names[-1] == "done"

    done_payload = next(payload for name, payload in events if name == "done")
    assert done_payload["reply"]
    assert done_payload["task"]["status"] == "completed"


def test_stream_chat_concatenated_deltas_match_reply(streaming_client) -> None:
    """The streamed delta chunks must reconstruct the final reply exactly."""
    with streaming_client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "再来一次", "session_id": "stream-roundtrip"},
    ) as resp:
        body = "".join(chunk for chunk in resp.iter_text())

    events = _parse_sse(body)
    deltas = [payload["text"] for name, payload in events if name == "delta"]
    done_payload = next(payload for name, payload in events if name == "done")
    assert "".join(deltas) == done_payload["reply"]
