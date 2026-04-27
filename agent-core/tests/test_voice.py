from __future__ import annotations

import pytest

from app.services.voice import (
    _decode_minimax_tts_response,
    _extract_modelscope_audio_url,
    _resolve_edge_voice_id,
    _is_silent_audio,
    _minimax_voice_controls,
    _resolve_modelscope_voice_instruction,
    _speed_to_edge_rate,
)


def test_decode_minimax_tts_hex_response() -> None:
    audio, mime_type = _decode_minimax_tts_response(
        {
            "data": {"audio": "494433"},
            "extra_info": {"audio_format": "mp3"},
            "base_resp": {"status_code": 0, "status_msg": "success"},
        }
    )

    assert audio == b"ID3"
    assert mime_type == "audio/mpeg"


def test_decode_minimax_tts_error_response() -> None:
    with pytest.raises(RuntimeError, match="not support model"):
        _decode_minimax_tts_response(
            {
                "base_resp": {
                    "status_code": 2061,
                    "status_msg": "your current token plan not support model",
                }
            }
        )


def test_minimax_voice_controls_use_integer_payload_values() -> None:
    controls = _minimax_voice_controls(speed=1.0, pitch=1.05)

    assert controls == {"speed": 1, "pitch": 1, "vol": 1}
    assert all(isinstance(value, int) for value in controls.values())


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


def test_modelscope_audio_url_extraction_accepts_gradio_file_data() -> None:
    url = _extract_modelscope_audio_url(
        {
            "data": [
                {
                    "path": "/tmp/gradio/audio.wav",
                    "url": "https://iic-cosyvoice2-0-5b.ms.show/file=/tmp/gradio/audio.wav",
                }
            ]
        },
        "https://iic-cosyvoice2-0-5b.ms.show",
    )

    assert url == "https://iic-cosyvoice2-0-5b.ms.show/file=/tmp/gradio/audio.wav"


def test_modelscope_voice_aliases_become_instructions() -> None:
    assert _resolve_modelscope_voice_instruction("warm-girl") == "用温柔自然的中文女声朗读"
    assert _resolve_modelscope_voice_instruction("用活泼的语气朗读") == "用活泼的语气朗读"


def test_silent_wav_is_rejected() -> None:
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x00" * 24000)

    assert _is_silent_audio(buffer.getvalue(), "audio/wav") is True
