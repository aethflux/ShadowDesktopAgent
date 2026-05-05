"""Phase 4 — long-task knobs.

Covers the three regressions most likely to break the user-facing experience:
1. ``max_tool_iterations`` actually drives the loop (not the old hard-coded 6).
2. ``_safe_parse_plan`` tolerates the JSON shapes models commonly emit.
3. ``_should_plan_first`` keeps cheap chats cheap (no extra LLM call for
   "你好").
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.agents.llm_agent import _safe_parse_plan, _should_plan_first
from app.config import settings
from tests.test_streaming import _fake_chat_with_tool_call, _fake_plan, _parse_sse, _scripted_calls


# ---- _safe_parse_plan ---------------------------------------------------- #


def test_parse_plan_accepts_clean_json() -> None:
    parsed = _safe_parse_plan('{"plan": ["读取仓库", "运行测试"], "summary": "两步"}')
    assert parsed == {"plan": ["读取仓库", "运行测试"], "summary": "两步"}


def test_parse_plan_strips_json_fence() -> None:
    raw = '```json\n{"plan": ["a", "b"], "summary": "x"}\n```'
    parsed = _safe_parse_plan(raw)
    assert parsed == {"plan": ["a", "b"], "summary": "x"}


def test_parse_plan_extracts_json_with_trailing_prose() -> None:
    raw = 'Here is the plan: {"plan": ["a"], "summary": ""}\n希望对你有帮助。'
    parsed = _safe_parse_plan(raw)
    assert parsed == {"plan": ["a"], "summary": ""}


def test_parse_plan_caps_to_six_steps() -> None:
    raw = '{"plan": ["1","2","3","4","5","6","7","8"], "summary": ""}'
    parsed = _safe_parse_plan(raw)
    assert parsed is not None
    assert len(parsed["plan"]) == 6


def test_parse_plan_rejects_non_object() -> None:
    assert _safe_parse_plan("[1, 2, 3]") is None


def test_parse_plan_rejects_missing_plan_key() -> None:
    assert _safe_parse_plan('{"summary": "no list"}') is None


def test_parse_plan_handles_empty_input() -> None:
    assert _safe_parse_plan("") is None
    assert _safe_parse_plan("not json at all") is None


# ---- _should_plan_first heuristic ---------------------------------------- #


@pytest.fixture(autouse=True)
def _restore_plan_first(monkeypatch):
    monkeypatch.setattr(settings, "enable_plan_first", True)
    yield


def test_short_message_skips_plan() -> None:
    assert not _should_plan_first("你好")


def test_simple_chat_skips_plan() -> None:
    assert not _should_plan_first("现在几点了？")


def test_multistep_request_triggers_plan() -> None:
    assert _should_plan_first("帮我下载这个仓库并运行它的测试")


def test_disabled_setting_overrides_heuristic(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_plan_first", False)
    assert not _should_plan_first("帮我下载这个仓库并运行它的测试")


# ---- max_tool_iterations honoured ---------------------------------------- #


def test_max_tool_iterations_setting_caps_loop(monkeypatch) -> None:
    """If we set the cap to 1, the agent must give up after one round-trip
    instead of looping forever on a tool-only response. We use the same
    scripted unknown-tool fixture as the streaming test — its first reply is
    a tool call, second is a plain reply. A cap of 1 forces it to bail before
    the second call lands, surfacing the fallback string."""
    from app.main import app

    monkeypatch.setattr(settings, "max_tool_iterations", 1)
    monkeypatch.setattr(settings, "enable_plan_first", False)
    _scripted_calls["count"] = 0

    with patch("app.services.model_client.ModelClient.chat", new=_fake_chat_with_tool_call), \
         patch("app.agents.router.RouterAgent.plan", new=_fake_plan):
        with TestClient(app) as client:
            with client.stream(
                "POST",
                "/api/chat/stream",
                json={"message": "你好呀", "session_id": "max-iter-test"},
            ) as resp:
                body = "".join(chunk for chunk in resp.iter_text())

    events = _parse_sse(body)
    done = next(p for n, p in events if n == "done")
    # With cap=1 the loop runs once, gets a tool call, executes it, but never
    # re-asks the model for a follow-up reply. The fallback summariser kicks
    # in instead.
    assert "工具调用已结束" in done["reply"]
