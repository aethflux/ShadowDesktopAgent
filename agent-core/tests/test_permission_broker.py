"""Tests for the permission broker.

Two flavours:
1. Direct broker tests — verify deny-list / allow-list / per-session caching
   logic in isolation, without touching FastAPI.
2. SSE end-to-end tests — drive the streaming endpoint with a scripted LLM
   that asks for a forbidden cwd, then verify a ``permission_request`` event
   fires and that POSTing a decision unblocks the agent.
"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.services.permission_broker import (
    PermissionBroker,
    PermissionDenied,
)
from app.services.streaming_context import progress_cb_var, session_id_var


# ------------------------------------------------------------------ #
# Direct broker tests                                                #
# ------------------------------------------------------------------ #


@pytest.fixture
def isolated_settings(monkeypatch, tmp_path):
    """Snapshot mutable settings, restore after test."""
    monkeypatch.setattr(settings, "workspace_allowlist_json", "[]")
    monkeypatch.setattr(
        settings, "workspace_denylist_json",
        json.dumps([str(tmp_path / "denied")]),
    )
    monkeypatch.setattr(settings, "require_path_confirmation", True)
    monkeypatch.setattr(settings, "permission_request_timeout_seconds", 5)
    monkeypatch.setattr(
        settings, "command_workspace_root", tmp_path / "workspace",
    )
    (tmp_path / "workspace").mkdir(exist_ok=True)
    (tmp_path / "denied").mkdir(exist_ok=True)
    yield tmp_path


def test_path_inside_workspace_passes_without_prompt(isolated_settings) -> None:
    """The legacy command_workspace_root remains an implicit allow rule —
    paths inside it must not trigger a permission_request."""
    broker = PermissionBroker()
    inside = isolated_settings / "workspace" / "subdir"
    inside.mkdir(parents=True, exist_ok=True)

    # No progress_cb in context → if the broker tried to prompt, it would
    # raise. Reaching the return without exception confirms the allow path.
    asyncio.run(broker.check(inside, reason="test"))


def test_denylist_short_circuits_even_with_progress_cb(isolated_settings) -> None:
    """A path matching the deny-list must be rejected without ever invoking
    the progress callback — there's nothing for the user to decide."""
    broker = PermissionBroker()
    denied = isolated_settings / "denied" / "secret.txt"
    denied.parent.mkdir(parents=True, exist_ok=True)
    denied.write_text("x")

    calls: list[dict] = []

    async def fake_cb(event: dict) -> None:
        calls.append(event)

    async def run() -> None:
        token = progress_cb_var.set(fake_cb)
        try:
            with pytest.raises(PermissionDenied, match="deny-list"):
                await broker.check(denied, reason="test")
        finally:
            progress_cb_var.reset(token)

    asyncio.run(run())
    assert calls == []  # callback never fired


def test_no_streaming_session_falls_back_to_deny(isolated_settings) -> None:
    """Calling the broker outside a streaming session must hard-deny: there
    is no UI to ask, so legacy ``/api/chat`` keeps its strict behaviour."""
    broker = PermissionBroker()
    outside = Path.home()  # almost certainly outside the per-test workspace

    async def run() -> None:
        with pytest.raises(PermissionDenied, match="no streaming session"):
            await broker.check(outside, reason="test")

    asyncio.run(run())


def test_allow_session_caches_and_skips_subsequent_prompts(isolated_settings) -> None:
    """An ``allow_session`` decision should let the same path through on the
    next call without raising another permission_request."""
    broker = PermissionBroker()
    target = Path.home()
    prompts: list[dict] = []

    async def fake_cb(event: dict) -> None:
        prompts.append(event)
        # Resolve the future immediately as if the user clicked "allow_session".
        request_id = event["data"]["request_id"]
        broker.resolve(request_id, "allow_session")

    async def run() -> None:
        token_cb = progress_cb_var.set(fake_cb)
        token_session = session_id_var.set("alice")
        try:
            await broker.check(target, reason="first")
            await broker.check(target, reason="second")
        finally:
            progress_cb_var.reset(token_cb)
            session_id_var.reset(token_session)

    asyncio.run(run())
    assert len(prompts) == 1, "second call should hit the per-session cache"


def test_deny_decision_raises_permission_denied(isolated_settings) -> None:
    broker = PermissionBroker()
    target = Path.home()

    async def fake_cb(event: dict) -> None:
        request_id = event["data"]["request_id"]
        broker.resolve(request_id, "deny")

    async def run() -> None:
        token = progress_cb_var.set(fake_cb)
        try:
            with pytest.raises(PermissionDenied, match="user denied"):
                await broker.check(target, reason="test")
        finally:
            progress_cb_var.reset(token)

    asyncio.run(run())


def test_resolve_returns_false_for_unknown_request() -> None:
    """A late or stray /api/permissions/decide must not crash the server."""
    broker = PermissionBroker()
    assert broker.resolve("nonexistent", "deny") is False


# ------------------------------------------------------------------ #
# SSE end-to-end test                                                #
# ------------------------------------------------------------------ #


_PERM_TOOL_NAME = "terminal.run"
_perm_chat_calls = {"count": 0}


async def _fake_chat_terminal_outside(self, messages, tools=None, tool_choice="auto", temperature=0.2):
    """First call: ask the LLM to run a terminal command in a path outside
    the workspace. Second call: reply with plain text after the tool result."""
    _perm_chat_calls["count"] += 1
    if _perm_chat_calls["count"] == 1:
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "perm_call_1",
                                "type": "function",
                                "function": {
                                    "name": _PERM_TOOL_NAME,
                                    "arguments": json.dumps(
                                        {"command": "echo hi", "cwd": str(Path.home())}
                                    ),
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
            {"message": {"role": "assistant", "content": "好的。"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 6, "completion_tokens": 2},
    }


async def _fake_plan(self, message, tools, local_intent, **_kwargs):
    return None


def _parse_sse(body: str) -> list[tuple[str, dict]]:
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


def test_streaming_emits_permission_request_when_tool_targets_outside_path() -> None:
    """End-to-end: an LLM tool call to an outside cwd surfaces a
    ``permission_request`` on the SSE stream. We answer it from a background
    thread (mirroring the real /api/permissions/decide handler) and verify
    the agent ultimately surfaces a 'blocked' tool result."""
    from app.main import app
    from app.services.permission_broker import broker as global_broker

    _perm_chat_calls["count"] = 0
    decided: list[str] = []

    def watch_and_deny() -> None:
        # Poll the broker for a pending request, then resolve it with deny.
        # In the real product the user clicks a button which POSTs to
        # /api/permissions/decide; calling resolve directly is equivalent.
        for _ in range(80):  # ~8s budget
            if global_broker.pending_count() > 0:
                with global_broker._lock:  # noqa: SLF001 — test-only access
                    request_id = next(iter(global_broker._pending))
                global_broker.resolve(request_id, "deny")
                decided.append(request_id)
                return
            import time
            time.sleep(0.1)

    watcher = threading.Thread(target=watch_and_deny, daemon=True)

    with patch("app.services.model_client.ModelClient.chat", new=_fake_chat_terminal_outside), \
         patch("app.agents.router.RouterAgent.plan", new=_fake_plan):
        with TestClient(app) as client:
            watcher.start()
            with client.stream(
                "POST",
                "/api/chat/stream",
                json={"message": "在我的家目录跑一下 echo hi", "session_id": "perm-test"},
            ) as resp:
                assert resp.status_code == 200
                body = "".join(chunk for chunk in resp.iter_text())
            watcher.join(timeout=2)

    events = _parse_sse(body)
    names = [name for name, _ in events]

    assert "permission_request" in names, f"no permission_request emitted; saw {names}"
    perm_payload = next(p for n, p in events if n == "permission_request")
    assert perm_payload["request_id"]
    assert perm_payload["tool_name"] == _PERM_TOOL_NAME
    # The deny decision must propagate to the tool, surfacing as a structured
    # error string on the aggregated tool_call event.
    aggregated = next(p for n, p in events if n == "tool_call")
    assert "blocked" in aggregated["result"].lower(), aggregated["result"]
    assert decided, "background thread did not see the pending request"
