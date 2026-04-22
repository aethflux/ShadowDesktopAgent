"""Unit tests for the local intent router.

These tests exercise the deterministic keyword/weight path — no network, no
LLM. They should stay fast (<1s) so CI runs them on every commit.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.router import RouterAgent


@pytest.fixture(scope="module")
def router() -> RouterAgent:
    return RouterAgent()


def _testset() -> list[dict]:
    path = Path(__file__).parent.parent / "eval" / "intent_testset.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _testset())
def test_router_matches_expected_delegate(router: RouterAgent, case: dict) -> None:
    result = router.classify_local(case["message"])
    assert result.delegated_to == case["expected_delegate"], (
        f"message={case['message']!r} expected={case['expected_delegate']} got={result.delegated_to}"
    )


def test_attachment_biases_toward_screen_observation(router: RouterAgent) -> None:
    result = router.classify_local("帮我看看这个", has_attachments=True)
    assert result.intent == "screen_observation"
    assert result.delegated_to == "desktop-agent"


def test_empty_message_falls_back_to_conversation(router: RouterAgent) -> None:
    result = router.classify_local("")
    assert result.intent == "conversation"
    assert result.delegated_to == "companion-agent"
