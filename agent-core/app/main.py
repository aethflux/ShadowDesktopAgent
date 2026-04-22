from pathlib import Path
import uuid

import httpx
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.orchestrator import MultiAgentOrchestrator
from app.schemas import (
    ChatRequest,
    ChatResponse,
    ObservationRequest,
    ObservationResponse,
    UserProfile,
    VoiceTTSRequest,
    VoiceTTSResponse,
)
from app.services import voice as voice_service

app = FastAPI(title=settings.app_name)
orchestrator = MultiAgentOrchestrator()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*", settings.desktop_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure audio output dir exists for TTS.
_artifacts_dir = settings.screenshots_dir.resolve().parent
_audio_dir = _artifacts_dir / "audio"
_audio_dir.mkdir(parents=True, exist_ok=True)

app.mount(
    "/artifacts/screenshots",
    StaticFiles(directory=settings.screenshots_dir),
    name="screenshots",
)
app.mount(
    "/artifacts/audio",
    StaticFiles(directory=_audio_dir),
    name="audio",
)


@app.on_event("startup")
async def _startup() -> None:
    # Discover MCP tools lazily so a missing npx / server binary doesn't
    # prevent the HTTP server from booting.
    await orchestrator.bootstrap()


@app.on_event("shutdown")
async def _shutdown() -> None:
    await orchestrator.mcp.shutdown()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "provider": settings.provider, "model": settings.resolved_model()}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    return await orchestrator.handle_chat(request)


@app.get("/api/capabilities")
async def capabilities() -> dict:
    return {
        "tools": orchestrator.registry.names(),
        "mcp_servers": orchestrator.mcp.list_servers(),
        "skills": [s.model_dump() for s in orchestrator.skills.list_skills()],
    }


@app.get("/api/companion/strategy/{session_id}")
async def companion_strategy(session_id: str) -> dict:
    """Expose current engagement metrics for the active companion session."""
    return orchestrator.strategy.get_state(session_id)


@app.post("/api/companion/observe", response_model=ObservationResponse)
async def observe_screen(request: ObservationRequest) -> ObservationResponse:
    return await orchestrator.observe_screen(request)


@app.get("/api/profile/{session_id}", response_model=UserProfile)
async def profile(session_id: str) -> UserProfile:
    return orchestrator.get_profile(session_id)


@app.post("/api/voice/tts", response_model=VoiceTTSResponse)
async def tts(request: VoiceTTSRequest) -> VoiceTTSResponse:
    """Return browser-speech parameters by default.

    MiniMax TTS is optional and stays disabled unless a deployment explicitly
    enables it with working speech quota.
    """
    if not settings.enable_minimax_voice or not request.text.strip():
        # Browser Web Speech API — return params unchanged.
        return VoiceTTSResponse(
            text=request.text,
            engine="browser-speech",
            voice=request.voice,
            rate=request.rate,
            pitch=request.pitch,
        )

    audio_bytes, mime_type = await voice_service.synthesize_speech(
        text=request.text,
        voice=request.voice,
        speed=request.rate,   # Web Speech rate → MiniMax speed
        pitch=request.pitch,
    )

    # Write to artifacts/audio/ and return the URL for the frontend to <audio src>.
    filename = f"tts-{Path(__file__).resolve().stem}-{uuid.uuid4().hex[:8]}{voice_service.audio_ext_from_mime(mime_type)}"
    out_path = _audio_dir / filename
    out_path.write_bytes(audio_bytes)

    return VoiceTTSResponse(
        text=request.text,
        engine="minimax",
        voice=request.voice or settings.minimax_tts_voice_id,
        rate=request.rate,
        pitch=request.pitch,
        audio_url=f"/artifacts/audio/{filename}",
    )


@app.post("/api/voice/asr")
async def asr(file: UploadFile = File(...)) -> dict:
    """Transcribe an audio file via MiniMax ASR when explicitly enabled.

    Accepts multipart audio (mp3 / wav / m4a / ogg / flac).
    Returns ``{"text": "..."}``.
    """
    if not settings.enable_minimax_voice:
        raise HTTPException(503, "ASR is only available when ENABLE_MINIMAX_VOICE=true")

    contents = await file.read()
    if not contents:
        raise HTTPException(400, "empty audio file")

    try:
        text = await voice_service.recognize_speech(contents, filename=file.filename or "audio.mp3")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(int(exc.response.status_code), f"MiniMax ASR error: {exc.response.text}")
    except Exception as exc:
        raise HTTPException(500, f"ASR failed: {exc}")

    return {"text": text, "engine": "minimax"}


@app.post("/api/screen/capture")
async def capture_screen() -> dict[str, str]:
    result = orchestrator.registry.run("screen.capture", {})
    path = Path(result).resolve()
    relative_path = path.relative_to(settings.screenshots_dir.resolve()).as_posix()
    return {
        "type": "screenshot",
        "label": path.name,
        "path": str(path),
        "url": f"/artifacts/screenshots/{relative_path}",
    }
