"""Tests for dialogue-state routing: sticky fallback + window-aware arbitration.

A message with no keyword signal ("再跑一次", "继续") is usually a follow-up
to the previous turn. Stateless routing dumped it to the default companion
agent — which, with per-agent tool trimming, can no longer rescue a misroute
by calling terminal tools itself. These tests pin the fix:

  - sticky fallback resolves signal-free messages to the previous agent,
  - any keyword signal (and any decisive match) still wins over sticky,
  - sticky state is per-session and can be disabled by settings,
  - the LLM tiebreaker prompt carries the recent dialogue window.
"""
from __future__ import annotations

import pytest

from app.agents.router import RouterAgent
from app.config import settings
from app.orchestrator import MultiAgentOrchestrator
from app.schemas import MemoryItem


@pytest.fixture(scope="module")
def router() -> RouterAgent:
    return RouterAgent()


@pytest.fixture
def orch() -> MultiAgentOrchestrator:
    return MultiAgentOrchestrator()


async def _offline_plan(*_args, **_kwargs):
    return None


# ---- resolve_delegate ----------------------------------------------------- #


def test_signal_free_followup_sticks_to_previous(router: RouterAgent) -> None:
    local = router.classify_local("再跑一次")
    assert RouterAgent.resolve_delegate(local, "terminal-agent") == ("terminal-agent", "sticky")


def test_keyword_signal_overrides_sticky(router: RouterAgent) -> None:
    local = router.classify_local("陪我聊聊吧")
    assert RouterAgent.resolve_delegate(local, "terminal-agent") == ("companion-agent", "local")


def test_decisive_keyword_overrides_sticky(router: RouterAgent) -> None:
    local = router.classify_local("帮我截个图")
    assert RouterAgent.resolve_delegate(local, "terminal-agent") == ("desktop-agent", "local")


def test_no_previous_turn_keeps_default(router: RouterAgent) -> None:
    local = router.classify_local("继续")
    assert RouterAgent.resolve_delegate(local, None) == ("companion-agent", "local")


# ---- Orchestrator integration ---------------------------------------------- #


@pytest.mark.asyncio
async def test_route_is_sticky_across_turns(
    orch: MultiAgentOrchestrator, monkeypatch, tmp_session_id: str
) -> None:
    monkeypatch.setattr(orch.router, "plan", _offline_plan)

    first, _ = await orch._route("帮我跑一下 pytest", session_id=tmp_session_id)
    assert first.delegated_to == "terminal-agent"

    second, _ = await orch._route("再跑一次", session_id=tmp_session_id)
    assert second.delegated_to == "terminal-agent"
    assert "Sticky fallback" in second.reasoning


@pytest.mark.asyncio
async def test_sticky_disable_switch(
    orch: MultiAgentOrchestrator, monkeypatch, tmp_session_id: str
) -> None:
    monkeypatch.setattr(orch.router, "plan", _offline_plan)
    monkeypatch.setattr(settings, "router_sticky_fallback", False)

    await orch._route("帮我跑一下 pytest", session_id=tmp_session_id)
    second, _ = await orch._route("再跑一次", session_id=tmp_session_id)
    assert second.delegated_to == "companion-agent"


@pytest.mark.asyncio
async def test_sessions_do_not_leak_sticky_state(
    orch: MultiAgentOrchestrator, monkeypatch, tmp_session_id: str
) -> None:
    monkeypatch.setattr(orch.router, "plan", _offline_plan)

    await orch._route("帮我跑一下 pytest", session_id=f"{tmp_session_id}-a")
    other, _ = await orch._route("继续", session_id=f"{tmp_session_id}-b")
    assert other.delegated_to == "companion-agent"


# ---- Arbitration window ----------------------------------------------------- #


@pytest.mark.asyncio
async def test_plan_prompt_carries_dialogue_window(
    orch: MultiAgentOrchestrator, monkeypatch, tmp_session_id: str
) -> None:
    captured: dict[str, str] = {}

    async def capture(system_prompt: str, user_prompt: str):
        captured["user"] = user_prompt
        return None

    orch.memory.append(
        MemoryItem(session_id=tmp_session_id, role="user", content="帮我跑一下 pytest")
    )
    orch.memory.append(
        MemoryItem(session_id=tmp_session_id, role="assistant", content="跑完了，3 个失败。")
    )
    orch._session_last_delegate[tmp_session_id] = "terminal-agent"
    monkeypatch.setattr(orch.router.model_client, "complete_structured", capture)

    await orch._route("再跑一次", session_id=tmp_session_id)

    assert "Recent dialogue" in captured["user"]
    assert "帮我跑一下 pytest" in captured["user"]
    assert "Previous turn was handled by: terminal-agent" in captured["user"]
