"""Optional TTS (Edge / ModelScope / MiniMax / Gemini) + ASR service."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin, urlparse
import wave
from io import BytesIO

import httpx

from app.config import settings


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #

TTSEngine = Literal["browser-speech", "edge", "modelscope", "minimax", "gemini"]


@dataclass(frozen=True)
class TTSResult:
    audio: bytes
    mime_type: str
    engine: TTSEngine
    voice: str | None = None


def cloud_tts_enabled() -> bool:
    return (
        settings.enable_edge_tts
        or settings.enable_modelscope_tts
        or settings.enable_gemini_tts
        or settings.enable_minimax_voice
    )


def active_tts_engine() -> TTSEngine:
    if settings.enable_edge_tts:
        return "edge"
    if settings.enable_modelscope_tts:
        return "modelscope"
    if settings.enable_gemini_tts:
        return "gemini"
    if settings.enable_minimax_voice:
        return "minimax"
    return "browser-speech"


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def _mime_from_extension(ext: str) -> str:
    mapping = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".flac": "audio/flac",
        ".m4a": "audio/mp4",
    }
    return mapping.get(ext, "audio/mpeg")


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


def _wav_rms(audio: bytes) -> int:
    try:
        with wave.open(BytesIO(audio), "rb") as wav:
            sample_width = wav.getsampwidth()
            frames = wav.readframes(wav.getnframes())
    except wave.Error:
        return 1

    if sample_width != 2 or not frames:
        return 1

    total = 0
    count = 0
    for index in range(0, len(frames) - 1, 2):
        sample = int.from_bytes(frames[index:index + 2], "little", signed=True)
        total += sample * sample
        count += 1
    return int((total / max(1, count)) ** 0.5)


def _is_silent_audio(audio: bytes, mime_type: str) -> bool:
    if "wav" not in mime_type:
        return False
    return _wav_rms(audio) < 16


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
#  ModelScope TTS (CosyVoice2 Studio / Gradio)
# --------------------------------------------------------------------------- #

_MODELSCOPE_TTS_VOICE_INSTRUCTIONS = {
    "warm-girl": "用温柔自然的中文女声朗读",
    "girl": "用温柔自然的中文女声朗读",
    "sweet-lady": "用甜美清晰的中文女声朗读",
    "gentleman": "用沉稳自然的中文男声朗读",
    "male": "用沉稳自然的中文男声朗读",
}


def _resolve_modelscope_voice_instruction(requested: str | None) -> str:
    if not requested:
        return settings.modelscope_tts_instruction
    return _MODELSCOPE_TTS_VOICE_INSTRUCTIONS.get(requested.lower(), requested)


def _extract_modelscope_audio_url(payload: dict[str, Any], base_url: str) -> str:
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise RuntimeError("ModelScope TTS returned no audio data")

    audio_info = data[0]
    if not isinstance(audio_info, dict):
        raise RuntimeError("ModelScope TTS returned invalid audio data")

    url = audio_info.get("url") or audio_info.get("path")
    if not isinstance(url, str) or not url:
        raise RuntimeError("ModelScope TTS returned no audio URL")
    return url if url.startswith("http") else urljoin(base_url.rstrip("/") + "/", url)


async def modelscope_synthesize_speech(
    text: str,
    voice: str | None = None,
    _speed: float | None = None,
    _pitch: float | None = None,
) -> tuple[bytes, str, str]:
    """Call ModelScope CosyVoice2 Studio and return ``(audio_bytes, mime_type, voice)``."""
    if not text.strip():
        return b"", "audio/wav", settings.modelscope_tts_instruction

    base = settings.modelscope_tts_api_base.rstrip("/")
    instruction = _resolve_modelscope_voice_instruction(voice)
    payload = {
        "data": [
            text,
            settings.modelscope_tts_mode,
            "",
            None,
            None,
            instruction,
            settings.modelscope_tts_seed,
            False,
        ]
    }

    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        response = await client.post(f"{base}/run/generate_audio", json=payload)
        response.raise_for_status()
        audio_url = _extract_modelscope_audio_url(response.json(), base)

        audio_response = await client.get(audio_url)
        audio_response.raise_for_status()

    parsed = urlparse(audio_url)
    mime_type = (audio_response.headers.get("content-type") or "").split(";")[0].strip()
    if not mime_type or mime_type == "application/octet-stream":
        mime_type = _mime_from_extension(Path(parsed.path).suffix.lower())
    if _is_silent_audio(audio_response.content, mime_type):
        raise RuntimeError("ModelScope TTS returned silent audio")
    return audio_response.content, mime_type, instruction


# --------------------------------------------------------------------------- #
#  MiniMax TTS
# --------------------------------------------------------------------------- #

_MINIMAX_TTS_VOICE_IDS = {
    "warm-girl": "Chinese (Mandarin)_Warm_Girl",
    "sweet-lady": "Chinese (Mandarin)_Sweet_Lady",
    "gentleman": "Chinese (Mandarin)_Gentleman",
    "female-shaonv": "female-shaonv",
    "female-yujie": "female-yujie",
    "male-qn-qingse": "male-qn-qingse",
}


def _resolve_minimax_voice_id(requested: str | None) -> str:
    if not requested:
        return settings.minimax_tts_voice_id
    aliases = {
        "warm": "Chinese (Mandarin)_Warm_Girl",
        "girl": "Chinese (Mandarin)_Warm_Girl",
        "lady": "Chinese (Mandarin)_Sweet_Lady",
    }
    return _MINIMAX_TTS_VOICE_IDS.get(requested) or aliases.get(requested) or requested


def _minimax_voice_controls(speed: float | None, pitch: float | None) -> dict[str, int]:
    speed_value = settings.minimax_tts_speed if speed is None else speed
    if pitch is None:
        minimax_pitch = round(settings.minimax_tts_pitch)
    else:
        minimax_pitch = round((_clamp(pitch, 0.0, 2.0) - 1.0) * 12)
    return {
        "speed": int(round(_clamp(speed_value, 1.0, 2.0))),
        "pitch": int(_clamp(minimax_pitch, -12, 12)),
        "vol": int(round(_clamp(settings.minimax_tts_volume, 1.0, 10.0))),
    }


def _decode_minimax_tts_response(payload: dict[str, Any]) -> tuple[bytes, str]:
    base_resp = payload.get("base_resp") or {}
    if base_resp.get("status_code") not in (None, 0):
        raise RuntimeError(base_resp.get("status_msg") or "MiniMax TTS failed")

    data = payload.get("data") or {}
    audio_hex = data.get("audio")
    if not audio_hex:
        raise RuntimeError("MiniMax TTS returned no audio data")

    try:
        audio_bytes = bytes.fromhex(audio_hex)
    except ValueError as exc:
        raise RuntimeError("MiniMax TTS returned invalid hex audio") from exc

    extra_info = payload.get("extra_info") or {}
    audio_format = extra_info.get("audio_format", "mp3")
    mime = "audio/mpeg" if audio_format == "mp3" else f"audio/{audio_format}"
    return audio_bytes, mime


async def minimax_synthesize_speech(
    text: str,
    voice: str | None = None,
    speed: float | None = None,
    pitch: float | None = None,
) -> tuple[bytes, str]:
    """Call MiniMax TTS and return ``(audio_bytes, mime_type)``."""
    if not text.strip():
        return b"", "audio/mpeg"

    voice_id = _resolve_minimax_voice_id(voice)
    voice_controls = _minimax_voice_controls(speed=speed, pitch=pitch)
    payload: dict[str, Any] = {
        "model": settings.minimax_tts_model,
        "text": text,
        "stream": False,
        "language_boost": "Chinese",
        "output_format": "hex",
        "voiceSetting": {
            "voice_id": voice_id,
            **voice_controls,
        },
        "audioSetting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 1,
        },
    }

    headers = {
        "Authorization": f"Bearer {settings.resolved_api_key('minimax')}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{settings.minimax_voice_api_base.rstrip('/')}/t2a_v2",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return _decode_minimax_tts_response(response.json())


# --------------------------------------------------------------------------- #
#  Gemini TTS  (OpenAI-compatible /v1/audio/speech)
# --------------------------------------------------------------------------- #

_GEMINI_TTS_VOICES = {
    "kore": "Kore",
    "ash": "Ash",
    "fen": "Fen",
    "iris": "Iris",
    "lake": "Lake",
    "deck": "Deck",
    "dora": "Dora",
    "maverick": "Maverick",
}


def _resolve_gemini_voice(requested: str | None) -> str:
    if not requested:
        return settings.gemini_tts_voice
    return _GEMINI_TTS_VOICES.get(requested.lower()) or requested


async def gemini_synthesize_speech(
    text: str,
    voice: str | None = None,
    speed: float | None = None,
    _pitch: float | None = None,
) -> tuple[bytes, str]:
    """Call Gemini 3.1 Flash TTS and return ``(audio_bytes, mime_type)``.

    Endpoint: POST /v1beta/models/gemini-3.1-flash-tts-preview:generateContent
    Auth: x-goog-api-key header (not Bearer token).
    """
    if not text.strip():
        return b"", "audio/mpeg"

    voice_id = _resolve_gemini_voice(voice)
    raw_speed = speed if speed is not None else settings.gemini_tts_speed
    rate = round(_clamp(raw_speed, 0.25, 4.0), 2)

    api_key = settings.gemini_tts_api_key or settings.resolved_api_key("minimax")
    base = settings.gemini_tts_api_base.rstrip("/")
    model = settings.gemini_tts_model  # e.g. "google/gemini-3.1-flash-tts-preview"

    # Strip provider prefix if present (e.g. "google/gemini-3.1-flash-tts-preview" -> "gemini-3.1-flash-tts-preview")
    model_name = model.split("/")[-1] if "/" in model else model

    endpoint = f"{base}/v1beta/models/{model_name}:generateContent"

    payload = {
        "contents": [{"role": "user", "parts": [{"text": f"{text}\n\nSpeaking rate: {rate}x."}]}],
        "generationConfig": {
            "temperature": 0.9,
            "topP": 0.95,
            "topK": 40,
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": voice_id,
                    }
                }
            },
        },
    }

    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(endpoint, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()

        # Gemini TTS returns audio as base64 in candidates[0].content.parts[0].inlineData.data
        candidates = result.get("candidates", [])
        if not candidates:
            raise RuntimeError("Gemini TTS returned no audio data")

        content = candidates[0].get("content", {})
        parts = content.get("parts", [])
        if not parts:
            raise RuntimeError("Gemini TTS returned no audio data")

        inline_data = parts[0].get("inlineData", {})
        audio_base64 = inline_data.get("data", "")
        if not audio_base64:
            raise RuntimeError("Gemini TTS returned no audio data")

        import base64
        audio_bytes = base64.b64decode(audio_base64)
        mime = inline_data.get("mimeType", f"audio/{settings.gemini_tts_output_format}")
        return audio_bytes, mime


# --------------------------------------------------------------------------- #
#  Unified entry point
# --------------------------------------------------------------------------- #

async def synthesize_speech(
    text: str,
    voice: str | None = None,
    speed: float | None = None,
    pitch: float | None = None,
) -> TTSResult:
    """Call the enabled TTS provider and return audio metadata.

    Edge Neural TTS is preferred when enabled. Browser speech remains the
    fallback when cloud TTS is disabled or every cloud provider fails.
    """
    if settings.enable_edge_tts:
        try:
            audio, mime_type, resolved_voice = await edge_synthesize_speech(
                text, voice=voice, speed=speed, _pitch=pitch
            )
            return TTSResult(audio, mime_type, "edge", resolved_voice)
        except Exception:
            pass

    if settings.enable_modelscope_tts:
        try:
            audio, mime_type, resolved_voice = await modelscope_synthesize_speech(
                text, voice=voice, _speed=speed, _pitch=pitch
            )
            return TTSResult(audio, mime_type, "modelscope", resolved_voice)
        except Exception:
            pass

    if settings.enable_gemini_tts:
        try:
            audio, mime_type = await gemini_synthesize_speech(
                text, voice=voice, speed=speed, pitch=pitch
            )
            return TTSResult(audio, mime_type, "gemini", voice or settings.gemini_tts_voice)
        except Exception:
            pass

    if settings.enable_minimax_voice:
        try:
            audio, mime_type = await minimax_synthesize_speech(
                text, voice=voice, speed=speed, pitch=pitch
            )
            return TTSResult(audio, mime_type, "minimax", voice or settings.minimax_tts_voice_id)
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
