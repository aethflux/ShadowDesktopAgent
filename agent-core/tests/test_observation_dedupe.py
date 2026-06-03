from __future__ import annotations

import pytest

from app.orchestrator import MultiAgentOrchestrator, _is_similar_observation_reply
from app.schemas import ObservationRequest, ObservationState


def test_observation_similarity_catches_paraphrase() -> None:
    previous = "你现在还在 Edge 的 GitHub Actions 页面，先打开失败日志看看。"
    current = "你还在 Edge 浏览器里查看 GitHub Actions，先看最新失败日志就好。"

    assert _is_similar_observation_reply(current, previous)


def test_observation_similarity_ignores_prepended_nudge() -> None:
    previous = "我注意到你有一会儿没动静了，还好吗？\n\n你还在 README 页面，先检查启动说明。"
    current = "你依然在 README 文档里，可以继续核对启动说明。"

    assert _is_similar_observation_reply(current, previous)


@pytest.mark.asyncio
async def test_interval_observation_suppresses_similar_reply(tmp_session_id: str) -> None:
    orchestrator = MultiAgentOrchestrator()
    orchestrator.memory.save_observation_state(
        ObservationState(
            session_id=tmp_session_id,
            last_comment="你现在还在 Edge 的 GitHub Actions 页面，先打开失败日志看看。",
            last_topic="github-actions",
        )
    )

    class FakeDesktopAgent:
        async def observe_screen(self, **_kwargs):
            return (
                "你还在 Edge 浏览器里查看 GitHub Actions，先看最新失败日志就好。",
                [],
                "medium",
                True,
                "github-actions",
            )

    orchestrator.agents["desktop-agent"] = FakeDesktopAgent()

    response = await orchestrator.observe_screen(
        ObservationRequest(session_id=tmp_session_id, trigger="interval")
    )

    assert response.reply == ""
    assert response.should_speak is False
    assert response.significance == "low"
    assert response.task["status"] == "skipped"
    assert "similar" in response.task["title"]

    saved_state = orchestrator.memory.load_observation_state(tmp_session_id)
    assert saved_state.last_comment == "你现在还在 Edge 的 GitHub Actions 页面，先打开失败日志看看。"
