from __future__ import annotations

from app.services.voice import (
    _resolve_edge_voice_id,
    _speed_to_edge_rate,
)


def test_edge_voice_aliases_resolve_to_neural_voices() -> None:
    assert _resolve_edge_voice_id("warm-girl") == "zh-CN-XiaoxiaoNeural"
    assert _resolve_edge_voice_id("sweet-lady") == "zh-CN-XiaoyiNeural"
    assert _resolve_edge_voice_id("gentleman") == "zh-CN-YunxiNeural"
    assert _resolve_edge_voice_id("storyteller") == "zh-CN-YunjianNeural"
    assert _resolve_edge_voice_id("zh-CN-XiaoxiaoNeural") == "zh-CN-XiaoxiaoNeural"


def test_edge_rate_multiplier_uses_percent_syntax() -> None:
    assert _speed_to_edge_rate(1.0) == "+0%"
    assert _speed_to_edge_rate(1.12) == "+12%"
    assert _speed_to_edge_rate(0.9) == "-10%"
    assert _speed_to_edge_rate(2.0) == "+50%"
