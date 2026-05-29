"""Tests for the SSE streaming chat endpoint and the orchestrator's stream_chat."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.orchestrator import _chunk_text


@pytest.mark.parametrize("text,size,expected", [
    ("abcdefg", 3, ["abc", "def", "g"]),  # basic chunking
    ("", 3, []),                           # empty input → no chunks
    ("hi", 50, ["hi"]),                    # shorter than chunk size
    ("hello", 0, ["hello"]),               # zero size returns whole string
])
def test_chunk_text(text, size, expected) -> None:
    assert _chunk_text(text, size) == expected


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


# ---- Live tool progress events (tool_start / tool_end) ------------------- #


_UNKNOWN_TOOL = "stream_test.placeholder"
_scripted_calls = {"count": 0}


async def _fake_chat_with_tool_call(self, messages, tools=None, tool_choice="auto", temperature=0.2):
    """Fake ModelClient.chat that returns a tool call on the first call and a
    plain reply on the second.

    We deliberately invoke an unknown tool name. ``ToolRegistry.arun`` returns
    a deterministic error string for that case, so the test stays offline (no
    real terminal / screen capture) while the agent still goes through the
    full tool-call code path that emits start/end events.
    """
    _scripted_calls["count"] += 1
    if _scripted_calls["count"] == 1:
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": _UNKNOWN_TOOL,
                                    "arguments": "{}",
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 4},
        }
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": "好的，已经处理完了。"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 6, "completion_tokens": 4},
    }


def test_stream_chat_emits_tool_start_and_end_events() -> None:
    """When the agent calls a tool, the SSE stream should emit a ``tool_start``
    *before* the tool runs and a ``tool_end`` after, both carrying the same
    ``step_id``. The pre-existing ``tool_call`` aggregation event should still
    fire for back-compat with older renderers."""
    from app.main import app

    _scripted_calls["count"] = 0  # reset between tests

    with patch("app.services.model_client.ModelClient.chat", new=_fake_chat_with_tool_call), \
         patch("app.agents.router.RouterAgent.plan", new=_fake_plan):
        with TestClient(app) as client:
            with client.stream(
                "POST",
                "/api/chat/stream",
                # Plain greeting — routes to companion-agent and goes through
                # the LLM tool-call loop (not desktop-agent's observe_screen).
                json={"message": "你好呀", "session_id": "stream-tool-test"},
            ) as resp:
                assert resp.status_code == 200
                body = "".join(chunk for chunk in resp.iter_text())

    events = _parse_sse(body)
    names = [name for name, _ in events]

    assert "tool_start" in names, f"tool_start not emitted; saw {names}"
    assert "tool_end" in names, f"tool_end not emitted; saw {names}"
    # Strict order: start → end → first delta.
    assert names.index("tool_start") < names.index("tool_end") < names.index("delta")

    start_payload = next(p for n, p in events if n == "tool_start")
    end_payload = next(p for n, p in events if n == "tool_end")

    # Both events describe the same step.
    assert start_payload["step_id"] == end_payload["step_id"]
    assert start_payload["name"] == _UNKNOWN_TOOL
    assert end_payload["name"] == _UNKNOWN_TOOL
    # Unknown tool ⇒ registry surfaces failure; success flag must reflect it.
    assert end_payload["success"] is False
    assert isinstance(end_payload["duration_ms"], int)

    # Back-compat: the aggregated tool_call event still fires once after the
    # live pair, carrying the same step_id.
    aggregated = [p for n, p in events if n == "tool_call"]
    assert len(aggregated) == 1
    assert aggregated[0]["step_id"] == start_payload["step_id"]
