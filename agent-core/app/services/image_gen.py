"""Text-to-image generation via ModelScope API-Inference (async task flow).

Flow (ModelScope API-Inference):
  1. POST ``/images/generations`` with header ``X-ModelScope-Async-Mode: true``
     and body ``{"model", "prompt"}`` -> ``{"task_id"}``.
  2. Poll ``GET /tasks/{task_id}`` with header
     ``X-ModelScope-Task-Type: image_generation`` until
     ``task_status == "SUCCEED"`` -> ``output_images[0]`` (a URL).
  3. Download the image bytes from that URL.

Generated images are saved under ``<artifacts>/generated/`` and indexed in
``index.json`` so the desktop gallery can list / apply / delete them. The model
id and endpoint are configurable and reuse the ModelScope API key.

NOTE: the exact model id and response shape can vary by ModelScope account.
This follows the documented async API; if a live call fails, adjust
``IMAGE_MODEL`` / ``IMAGE_GENERATION_API_BASE`` (or the parsing here) — the rest
of the pipeline (save / index / serve) is provider-agnostic.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

import httpx

from app.config import settings


class ImageGenerationError(RuntimeError):
    """Raised for user-facing generation failures (config, empty prompt, etc.)."""


def generated_dir() -> Path:
    """Directory where generated images + the gallery index live."""
    target = settings.screenshots_dir.resolve().parent / "generated"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _index_path() -> Path:
    return generated_dir() / "index.json"


def _load_index() -> list[dict]:
    path = _index_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _save_index(records: list[dict]) -> None:
    _index_path().write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def image_generation_available() -> bool:
    """True when the feature is enabled *and* a ModelScope key is configured."""
    return settings.enable_image_generation and bool(settings.modelscope_api_key)


def list_gallery() -> list[dict]:
    return _load_index()


def delete_image(image_id: str) -> bool:
    """Remove a generated image (file + index entry). Returns False if unknown."""
    records = _load_index()
    kept = [r for r in records if r.get("id") != image_id]
    if len(kept) == len(records):
        return False
    for record in records:
        if record.get("id") == image_id and record.get("filename"):
            try:
                (generated_dir() / record["filename"]).unlink(missing_ok=True)
            except OSError:
                pass
    _save_index(kept)
    return True


async def _modelscope_generate(prompt: str) -> tuple[bytes, str]:
    """Run the ModelScope async text-to-image task and return (bytes, mime)."""
    base = settings.image_generation_api_base.rstrip("/")
    model = settings.image_model
    headers = {"Authorization": f"Bearer {settings.modelscope_api_key}"}

    async with httpx.AsyncClient(timeout=60) as client:
        submit = await client.post(
            f"{base}/images/generations",
            headers={
                **headers,
                "Content-Type": "application/json",
                "X-ModelScope-Async-Mode": "true",
            },
            json={"model": model, "prompt": prompt},
        )
        submit.raise_for_status()
        task_id = submit.json().get("task_id")
        if not task_id:
            raise ImageGenerationError(
                f"ModelScope returned no task_id: {submit.text[:200]}"
            )

        poll_headers = {**headers, "X-ModelScope-Task-Type": "image_generation"}
        deadline = time.monotonic() + settings.image_generation_timeout_seconds
        while time.monotonic() < deadline:
            await asyncio.sleep(3.0)
            poll = await client.get(f"{base}/tasks/{task_id}", headers=poll_headers)
            poll.raise_for_status()
            data = poll.json()
            status = str(data.get("task_status", "")).upper()
            if status == "SUCCEED":
                images = data.get("output_images") or []
                if not images:
                    raise ImageGenerationError(
                        "ModelScope task succeeded but returned no images."
                    )
                download = await client.get(images[0], timeout=120)
                download.raise_for_status()
                mime = (
                    download.headers.get("content-type", "image/png")
                    .split(";")[0]
                    .strip()
                )
                return download.content, mime
            if status in {"FAILED", "FAIL", "ERROR"}:
                raise ImageGenerationError(
                    f"ModelScope generation failed: {data.get('message') or data}"
                )

    raise ImageGenerationError(
        f"Image generation timed out after {settings.image_generation_timeout_seconds}s."
    )


_EXT_BY_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
}


async def generate_and_save(prompt: str, purpose: str = "other") -> dict:
    """Generate an image, persist it, append to the gallery index, return the record."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ImageGenerationError("Prompt is empty.")
    if not settings.enable_image_generation:
        raise ImageGenerationError(
            "Image generation is disabled (ENABLE_IMAGE_GENERATION=false)."
        )
    if not settings.modelscope_api_key:
        raise ImageGenerationError("MODELSCOPE_API_KEY is not configured.")

    image_bytes, mime = await _modelscope_generate(prompt)

    image_id = uuid.uuid4().hex[:12]
    filename = f"gen-{image_id}{_EXT_BY_MIME.get(mime, '.png')}"
    (generated_dir() / filename).write_bytes(image_bytes)

    record = {
        "id": image_id,
        "filename": filename,
        "url": f"/artifacts/generated/{filename}",
        "prompt": prompt,
        "purpose": purpose if purpose in {"scene", "avatar", "other"} else "other",
        "model": settings.image_model,
        "created_at": int(time.time()),
    }
    records = _load_index()
    records.insert(0, record)
    _save_index(records)
    return record
