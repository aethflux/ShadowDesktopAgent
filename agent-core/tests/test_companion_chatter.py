from __future__ import annotations

import pytest

from app.config import settings
from app.orchestrator import MultiAgentOrchestrator
from app.schemas import ChatterRequest
from app.services import news as news_mod


class FakeCompanion:
    """Stand-in companion agent: returns a unique line per call so the
    proactive dedup doesn't collapse distinct sources during a test."""

    def __init__(self) -> None:
        self.calls = 0

    async def compose_line(self, instruction: str, context: str = "") -> str:
        self.calls += 1
        return f"主动聊天第 {self.calls} 句～"


@pytest.mark.asyncio
async def test_chatter_disabled_stays_silent(monkeypatch, tmp_session_id: str) -> None:
    monkeypatch.setattr(settings, "enable_proactive_chatter", False)
    orch = MultiAgentOrchestrator()
    resp = await orch.companion_chatter(ChatterRequest(session_id=tmp_session_id))
    assert resp.should_speak is False
    assert resp.source == "none"


@pytest.mark.asyncio
async def test_chatter_cadence_gates_interval(tmp_session_id: str) -> None:
    orch = MultiAgentOrchestrator()
    orch.agents["companion-agent"] = FakeCompanion()

    first = await orch.companion_chatter(
        ChatterRequest(session_id=tmp_session_id, trigger="interval")
    )
    assert first.should_speak is True
    assert first.reply

    # A second interval tick immediately after is within the min interval and
    # must stay silent (no nattering).
    second = await orch.companion_chatter(
        ChatterRequest(session_id=tmp_session_id, trigger="interval")
    )
    assert second.should_speak is False


@pytest.mark.asyncio
async def test_chatter_rotates_sources_on_manual(monkeypatch, tmp_session_id: str) -> None:
    orch = MultiAgentOrchestrator()
    orch.agents["companion-agent"] = FakeCompanion()

    async def fake_headline():
        return news_mod.Headline("某开源项目发布了新版本", "", "hn")

    monkeypatch.setattr(news_mod, "random_headline", fake_headline)

    # Manual trigger bypasses the cadence gate, so we can watch the source
    # round-robin: memory → time → news.
    sources = []
    for _ in range(3):
        resp = await orch.companion_chatter(
            ChatterRequest(session_id=tmp_session_id, trigger="manual")
        )
        sources.append(resp.source)
    assert sources == ["memory", "time", "news"]


@pytest.mark.asyncio
async def test_news_source_falls_back_when_unavailable(monkeypatch, tmp_session_id: str) -> None:
    orch = MultiAgentOrchestrator()
    orch.agents["companion-agent"] = FakeCompanion()

    async def no_headline():
        return None

    monkeypatch.setattr(news_mod, "random_headline", no_headline)

    # Advance the source cursor to the "news" slot (index 2), then call it.
    orch.strategy.next_chatter_source(tmp_session_id, ["memory", "time", "news"])
    orch.strategy.next_chatter_source(tmp_session_id, ["memory", "time", "news"])
    resp = await orch.companion_chatter(
        ChatterRequest(session_id=tmp_session_id, trigger="manual")
    )
    # News had nothing → it should fall back to a memory call-back, not go silent.
    assert resp.should_speak is True
    assert resp.source == "memory"
