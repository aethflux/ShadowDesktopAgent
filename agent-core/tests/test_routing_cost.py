"""Tests for the decisive-keyword routing cost optimization.

The orchestrator skips the LLM second-opinion (``router.plan``) when the local
keyword classifier is *decisive* — it matched a near-unambiguous keyword.
This must:
  - flag decisive vs ambiguous correctly (keyword-driven, not score-driven),
  - actually skip the LLM call when decisive,
  - still consult the LLM on ambiguous/multi-intent messages,
  - honour the disable switch.
"""
from __future__ import annotations

import pytest

from app.agents.router import RouterAgent
from app.config import settings
from app.orchestrator import MultiAgentOrchestrator


@pytest.fixture(scope="module")
def router() -> RouterAgent:
    return RouterAgent()


# ---- Router: decisive flag ---------------------------------------------- #


@pytest.mark.parametrize("message", [
    "帮我跑一下 pytest",
    "帮我截个图看看",
    "调整一下你的人设，更温柔点",
    "用 npm 装个依赖",
])
def test_high_specificity_keyword_is_decisive(router: RouterAgent, message: str) -> None:
    assert router.classify_local(message).decisive is True


@pytest.mark.parametrize("message", [
    "在终端里看看屏幕上的输出",   # multi-intent: terminal vs screen
    "今天 git 上同事又摸鱼了哈哈",  # chit-chat with a stray keyword
    "帮我看看屏幕上的代码哪里错了",  # screen vs coding
    "你好呀",                      # no operational keyword at all
    "记住我习惯用 vim 写代码",      # memory vs coding
])
def test_ambiguous_message_is_not_decisive(router: RouterAgent, message: str) -> None:
    assert router.classify_local(message).decisive is False


def test_generic_keyword_alone_is_not_decisive(router: RouterAgent) -> None:
    """A bare ``git`` / ``代码`` must not be decisive — those words appear in
    banter and multi-intent sentences where the LLM tiebreaker still helps."""
    assert router.classify_local("我最近在学做饭，和写代码没关系").decisive is False


# ---- Orchestrator: skip behaviour --------------------------------------- #


@pytest.fixture
def orch() -> MultiAgentOrchestrator:
    # Construction is side-effect free (no MCP bootstrap), per existing tests.
    return MultiAgentOrchestrator()


@pytest.mark.asyncio
async def test_route_skips_plan_when_decisive(orch: MultiAgentOrchestrator, monkeypatch) -> None:
    calls = {"plan": 0}

    async def fake_plan(*args, **kwargs):
        calls["plan"] += 1
        return None

    monkeypatch.setattr(settings, "router_skip_plan_when_decisive", True)
    monkeypatch.setattr(orch.router, "plan", fake_plan)

    trace, local_intent = await orch._route("帮我跑一下 pytest", has_attachments=False)

    assert local_intent.decisive is True
    assert calls["plan"] == 0, "decisive route should not call the LLM planner"
    assert trace.delegated_to == "terminal-agent"


@pytest.mark.asyncio
async def test_route_consults_plan_when_ambiguous(orch: MultiAgentOrchestrator, monkeypatch) -> None:
    calls = {"plan": 0}

    async def fake_plan(*args, **kwargs):
        calls["plan"] += 1
        return None

    monkeypatch.setattr(settings, "router_skip_plan_when_decisive", True)
    monkeypatch.setattr(orch.router, "plan", fake_plan)

    _trace, local_intent = await orch._route("在终端里看看屏幕上的输出", has_attachments=False)

    assert local_intent.decisive is False
    assert calls["plan"] == 1, "ambiguous route must consult the LLM planner"


@pytest.mark.asyncio
async def test_disable_switch_forces_plan(orch: MultiAgentOrchestrator, monkeypatch) -> None:
    calls = {"plan": 0}

    async def fake_plan(*args, **kwargs):
        calls["plan"] += 1
        return None

    # Even a decisive message must consult the planner when the switch is off.
    monkeypatch.setattr(settings, "router_skip_plan_when_decisive", False)
    monkeypatch.setattr(orch.router, "plan", fake_plan)

    await orch._route("帮我跑一下 pytest", has_attachments=False)

    assert calls["plan"] == 1
