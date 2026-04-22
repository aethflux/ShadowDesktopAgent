"""Optional MiniMax TTS + ASR service.

The default product path uses browser speech on the desktop side. This module
remains only as an opt-in backend integration for deployments that explicitly
have MiniMax speech quota.

Enable with ``ENABLE_MINIMAX_VOICE=true`` in your ``.env``.

TTS endpoint : ``POST https://api.minimax.chat/v1/t2a_v2``
ASR endpoint : ``POST https://api.minimax.chat/v1/asr``

Both use the same ``MINIMAX_API_KEY`` as the LLM calls, so no extra credentials
are needed beyond what you already configured for the MiniMax provider.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from app.config import settings


# --------------------------------------------------------------------------- #
#  MiniMax TTS
# --------------------------------------------------------------------------- #

_MINIMAX_TTS_VOICE_IDS = {
    "female-tianmei": "female-tianmei",
    "female-yunxi":    "female-yunxi",
    "male-yunyang":    "male-yunyang",
}


def _resolve_voice_id(requested: str | None) -> str:
    if not requested:
        return settings.minimax_tts_voice_id
    # Aliases: allow short names.
    aliases = {
        "tianmei": "female-tianmei",
        "yunxi":   "female-yunxi",
        "yunyang": "male-yunyang",
    }
    return _MINIMAX_TTS_VOICE_IDS.get(requested) or aliases.get(requested) or requested


async def synthesize_speech(
    text: str,
    voice: str | None = None,
    speed: float | None = None,
    pitch: float | None = None,
) -> tuple[bytes, str]:
    """Call MiniMax TTS and return ``(audio_bytes, mime_type)``.

    Raises ``RuntimeError`` on API failure.
    """
    if not text.strip():
        return b"", "audio/mpeg"

    voice_id = _resolve_voice_id(voice)
    payload: dict[str, Any] = {
        "model": settings.minimax_tts_model,
        "text": text,
        "stream": False,
        "voice_setting": {
            "voice_id": voice_id,
            "speed":      speed     if speed     is not None else settings.minimax_tts_speed,
            "pitch":      pitch     if pitch     is not None else settings.minimax_tts_pitch,
            "volume":               settings.minimax_tts_volume,
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate":     128000,
            "format":      "mp3",
        },
    }

    headers = {
        "Authorization": f"Bearer {settings.resolved_api_key('minimax')}",
        "Content-Type":  "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{settings.minimax_api_base}/t2a_v2",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "audio/mpeg")
        return response.content, content_type


# --------------------------------------------------------------------------- #
#  MiniMax ASR
# --------------------------------------------------------------------------- #

async def recognize_speech(audio_bytes: bytes, filename: str = "audio.mp3") -> str:
    """Transcribe an audio file via MiniMax ASR.

    Returns the transcribed text. Raises ``RuntimeError`` on API failure.
    """
    if not audio_bytes:
        return ""

    # MiniMax ASR accepts: mp3 / wav / m4a / ogg / flac
    import mimetypes
    ext = Path(filename).suffix.lower()
    mime = mimetypes.types_map.get(ext, "audio/mpeg")

    files = {"file": (filename, audio_bytes, mime)}
    data = {"model": settings.minimax_asr_model, "language_boost": "zh"}
    headers = {"Authorization": f"Bearer {settings.resolved_api_key('minimax')}"}

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{settings.minimax_api_base}/asr",
            headers=headers,
            files=files,
            data=data,
        )
        response.raise_for_status()
        result = response.json()
        # MiniMax ASR returns { "text": "..." }
        return result.get("text", "").strip()


# --------------------------------------------------------------------------- #
#  Audio file utilities
# --------------------------------------------------------------------------- #

def audio_ext_from_mime(mime_type: str) -> str:
    """Map a MIME type to a common file extension."""
    mapping = {
        "audio/mpeg":   ".mp3",
        "audio/wav":    ".wav",
        "audio/x-wav":  ".wav",
        "audio/mp4":    ".m4a",
        "audio/ogg":    ".ogg",
        "audio/flac":   ".flac",
    }
    return mapping.get(mime_type, ".mp3")
