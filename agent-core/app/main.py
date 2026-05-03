import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.logging import get_logger
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
from app.rate_limit import TokenBucketRateLimiter
from app.services import voice as voice_service
from app.services.model_client import close_http_client

logger = get_logger("main")
orchestrator = MultiAgentOrchestrator()


def _cleanup_old_audio(directory: Path, retention_hours: int) -> int:
    """Delete TTS audio files older than ``retention_hours``.

    Returns the number of files removed. Designed to run at startup so audio
    artifacts from previous sessions don't accumulate forever.
    """
    if retention_hours <= 0 or not directory.exists():
        return 0
    cutoff = time.time() - retention_hours * 3600
    removed = 0
    for entry in directory.iterdir():
        if not entry.is_file():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
        except OSError as exc:
            logger.debug("Could not remove stale audio %s: %s", entry, exc)
    if removed:
        logger.info("Cleaned up %d stale TTS audio file(s)", removed)
    return removed


def _allowed_origins() -> list[str]:
    if settings.desktop_origin == "*":
        return ["*"]
    return sorted(
        {
            settings.desktop_origin.rstrip("/"),
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        }
    )


# Ensure audio output dir exists for TTS — defined before the lifespan so
# the cleanup helper can capture it without a forward reference.
_artifacts_dir = settings.screenshots_dir.resolve().parent
_audio_dir = _artifacts_dir / "audio"
_audio_dir.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Discover MCP tools lazily so a missing npx / server binary doesn't
    # prevent the HTTP server from booting.
    await orchestrator.bootstrap()
    _cleanup_old_audio(_audio_dir, settings.tts_audio_retention_hours)
    try:
        yield
    finally:
        await orchestrator.mcp.shutdown()
        await close_http_client()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=settings.desktop_origin != "*",
    allow_methods=["*"],
    allow_headers=["*"],
)

if settings.rate_limit_capacity > 0:
    app.add_middleware(
        TokenBucketRateLimiter,
        capacity=settings.rate_limit_capacity,
        refill_per_second=settings.rate_limit_refill_per_second,
    )

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

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "provider": settings.provider, "model": settings.resolved_model()}


@app.get("/api/ready")
async def ready() -> dict:
    """Detailed readiness diagnostics for the desktop client / monitoring.

    Unlike ``/health`` this reports the *actual* state of every subsystem
    (vision, semantic memory, MCP servers, tool registry) so the UI can
    render an accurate status bar instead of guessing.
    """
    desktop_agent = orchestrator.agents["desktop-agent"]
    mcp_servers = orchestrator.mcp.list_servers()
    return {
        "status": "ok",
        "uptime_hint": "see process supervisor for actual uptime",
        "model": {
            "provider": settings.provider,
            "name": settings.resolved_model(),
            "vision_provider": settings.vision_provider,
            "vision_model": settings.vision_model,
            "vision_supported": desktop_agent.vision_client.supports_vision(),
        },
        "memory": {
            "semantic_enabled": settings.enable_semantic_memory,
            "semantic_available": orchestrator.memory.vector.available,
            "embedding_provider": settings.embedding_provider,
        },
        "tools": {
            "count": len(orchestrator.registry.names()),
            "names": orchestrator.registry.names(),
        },
        "mcp": {
            "registered": len(mcp_servers),
            "running": sum(1 for s in mcp_servers if s.get("running")),
            "servers": mcp_servers,
        },
        "skills": {
            "count": len(orchestrator.skills.list_skills()),
        },
        "voice": {
            "cloud_tts_enabled": voice_service.cloud_tts_enabled(),
            "tts_engine": voice_service.active_tts_engine(),
            "asr_enabled": settings.enable_minimax_voice,
        },
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    return await orchestrator.handle_chat(request)


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """Server-Sent Events variant of ``/api/chat``.

    Emits one SSE event per stage so the desktop client can render a typing
    indicator, tool-call timeline, and progressive reply text without waiting
    for the entire turn to complete.
    """

    async def event_source():
        async for event in orchestrator.stream_chat(request):
            payload = json.dumps(event["data"], ensure_ascii=False)
            yield f"event: {event['event']}\ndata: {payload}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            # Disable buffering proxies so each event flushes immediately.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/capabilities")
async def capabilities() -> dict:
    desktop_agent = orchestrator.agents["desktop-agent"]
    orchestrator.skills.reload()
    return {
        "tools": orchestrator.registry.names(),
        "mcp_servers": orchestrator.mcp.list_servers(),
        "skills": [s.model_dump() for s in orchestrator.skills.list_skills()],
        "provider": settings.provider,
        "model": settings.resolved_model(),
        "vision_provider": settings.vision_provider,
        "vision_model": settings.vision_model,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "features": {
            "vision": desktop_agent.vision_client.supports_vision(),
            "browser_speech": True,
            "cloud_tts": voice_service.cloud_tts_enabled(),
            "tts_engine": voice_service.active_tts_engine(),
            "tts_note": (
                "Browser speech fallback is active because cloud TTS is disabled."
                if not voice_service.cloud_tts_enabled()
                else "Cloud TTS is enabled and will fall back to browser speech on failure."
            ),
            "semantic_memory": settings.enable_semantic_memory,
        },
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
    """Return cloud TTS audio when enabled.

    Edge / ModelScope / MiniMax / Gemini TTS is optional. Browser speech stays as the
    fallback when cloud generation fails.
    """
    use_cloud = voice_service.cloud_tts_enabled() and request.text.strip()
    if not use_cloud:
        return VoiceTTSResponse(
            text=request.text,
            engine="browser-speech",
            voice=request.voice,
            rate=request.rate,
            pitch=request.pitch,
        )

    try:
        tts_result = await voice_service.synthesize_speech(
            text=request.text,
            voice=request.voice,
            speed=request.rate,
            pitch=request.pitch,
        )
    except Exception:
        return VoiceTTSResponse(
            text=request.text,
            engine="browser-speech",
            voice=request.voice,
            rate=request.rate,
            pitch=request.pitch,
        )

    if not tts_result.audio:
        return VoiceTTSResponse(
            text=request.text,
            engine="browser-speech",
            voice=request.voice,
            rate=request.rate,
            pitch=request.pitch,
        )

    filename = f"tts-{uuid.uuid4().hex[:8]}{voice_service.audio_ext_from_mime(tts_result.mime_type)}"
    out_path = _audio_dir / filename
    out_path.write_bytes(tts_result.audio)

    return VoiceTTSResponse(
        text=request.text,
        engine=tts_result.engine,
        voice=tts_result.voice,
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
    if not orchestrator.registry.has("screen.capture"):
        raise HTTPException(503, "Screen capture tool is not available in this environment.")
    result = orchestrator.registry.run("screen.capture", {})
    try:
        path = Path(result).resolve(strict=True)
        relative_path = path.relative_to(settings.screenshots_dir.resolve()).as_posix()
    except (OSError, ValueError) as exc:
        raise HTTPException(500, f"Screen capture failed: {result or exc}")
    return {
        "type": "screenshot",
        "label": path.name,
        "path": str(path),
        "url": f"/artifacts/screenshots/{relative_path}",
    }
