"""Edge Neural TTS + MiniMax ASR service.

TTS is Edge-only (Microsoft Neural voices), with browser Speech Synthesis as
the offline fallback. ASR (speech-to-text) is an optional MiniMax endpoint,
gated on ``enable_minimax_voice`` and dormant unless a client posts audio.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

from app.config import settings


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #

TTSEngine = Literal["browser-speech", "edge"]


@dataclass(frozen=True)
class TTSResult:
    audio: bytes
    mime_type: str
    engine: TTSEngine
    voice: str | None = None


def cloud_tts_enabled() -> bool:
    return settings.enable_edge_tts


def active_tts_engine() -> TTSEngine:
    return "edge" if settings.enable_edge_tts else "browser-speech"


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def audio_ext_from_mime(mime_type: str) -> str:
    mapping = {
        "audio/mpeg": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/mp4": ".m4a",
        "audio/ogg": ".ogg",
        "audio/flac": ".flac",
    }
    return mapping.get(mime_type, ".mp3")


def _speed_to_edge_rate(speed: float | None) -> str:
    """Convert API rate multiplier to Edge TTS percentage syntax."""
    if speed is None:
        return settings.edge_tts_rate
    percent = int(round((_clamp(speed, 0.5, 1.5) - 1.0) * 100))
    return f"{percent:+d}%"


# --------------------------------------------------------------------------- #
#  Edge Neural TTS
# --------------------------------------------------------------------------- #

_EDGE_TTS_VOICE_IDS = {
    "warm-girl": "zh-CN-XiaoxiaoNeural",
    "girl": "zh-CN-XiaoxiaoNeural",
    "streamer": "zh-CN-XiaoxiaoNeural",
    "sweet-lady": "zh-CN-XiaoyiNeural",
    "soft": "zh-CN-XiaoyiNeural",
    "gentleman": "zh-CN-YunxiNeural",
    "male": "zh-CN-YunxiNeural",
    "storyteller": "zh-CN-YunjianNeural",
}


def _resolve_edge_voice_id(requested: str | None) -> str:
    if not requested:
        return settings.edge_tts_voice
    return _EDGE_TTS_VOICE_IDS.get(requested.lower(), requested)


async def edge_synthesize_speech(
    text: str,
    voice: str | None = None,
    speed: float | None = None,
    _pitch: float | None = None,
) -> tuple[bytes, str, str]:
    """Call Edge Neural TTS and return ``(audio_bytes, mime_type, voice_id)``."""
    if not text.strip():
        return b"", "audio/mpeg", settings.edge_tts_voice

    import edge_tts

    voice_id = _resolve_edge_voice_id(voice)
    communicate = edge_tts.Communicate(
        text=text,
        voice=voice_id,
        rate=_speed_to_edge_rate(speed),
        pitch=settings.edge_tts_pitch,
    )
    chunks: list[bytes] = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])

    audio = b"".join(chunks)
    if not audio:
        raise RuntimeError("Edge TTS returned no audio data")
    return audio, "audio/mpeg", voice_id


# --------------------------------------------------------------------------- #
#  Dispatcher
# --------------------------------------------------------------------------- #

async def synthesize_speech(
    text: str,
    voice: str | None = None,
    speed: float | None = None,
    pitch: float | None = None,
) -> TTSResult:
    """Synthesize speech with Edge Neural TTS when enabled.

    Browser speech remains the fallback when Edge TTS is disabled or the
    network call fails.
    """
    if settings.enable_edge_tts:
        try:
            audio, mime_type, resolved_voice = await edge_synthesize_speech(
                text, voice=voice, speed=speed, _pitch=pitch
            )
            return TTSResult(audio, mime_type, "edge", resolved_voice)
        except Exception:
            pass

    return TTSResult(b"", "audio/mpeg", "browser-speech", voice)


# --------------------------------------------------------------------------- #
#  MiniMax ASR
# --------------------------------------------------------------------------- #

async def recognize_speech(audio_bytes: bytes, filename: str = "audio.mp3") -> str:
    """Transcribe an audio file via MiniMax ASR.

    Returns the transcribed text. Raises ``RuntimeError`` on API failure.
    """
    if not audio_bytes:
        return ""

    import mimetypes
    ext = Path(filename).suffix.lower()
    mime = mimetypes.types_map.get(ext, "audio/mpeg")

    files = {"file": (filename, audio_bytes, mime)}
    data = {"model": settings.minimax_asr_model, "language_boost": "zh"}
    headers = {"Authorization": f"Bearer {settings.resolved_api_key('minimax')}"}

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{settings.minimax_voice_api_base.rstrip('/')}/asr",
            headers=headers,
            files=files,
            data=data,
        )
        response.raise_for_status()
        result = response.json()
        return result.get("text", "").strip()
