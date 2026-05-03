"""End-to-end tests for the /api/settings endpoints + the JSON overlay store.

The settings system has three pieces we want to lock down:
  1. A GET that returns the current effective view (no secrets).
  2. A PUT that validates, persists, and applies the patch in place — bad
     values must be rejected with a 400 and never persisted.
  3. A providers catalog endpoint that reflects which providers have keys.

We use ``TestClient`` against the real FastAPI app, then redirect the store's
JSON path to a temp file so the developer's persisted preferences aren't
clobbered.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A clean app with the settings overlay file redirected to ``tmp_path``."""
    from app.config import settings as live_settings
    from app.services import settings_store as store_module

    # Snapshot fields the test will mutate so we can roll back at the end —
    # the FastAPI ``app`` is a module-level singleton shared across tests.
    fields = (
        "provider", "vision_provider", "enable_semantic_memory",
        "semantic_top_k", "enable_edge_tts", "edge_tts_voice",
    )
    snapshot = {f: getattr(live_settings, f) for f in fields}

    # Redirect the singleton store to a fresh temp file.
    overlay = tmp_path / "user_settings.json"
    monkeypatch.setattr(store_module.store, "path", overlay)

    from app.main import app
    with TestClient(app) as c:
        yield c

    # Roll back any mutations the test made on the live settings.
    for field, value in snapshot.items():
        setattr(live_settings, field, value)


def test_get_settings_returns_full_view(client) -> None:
    body = client.get("/api/settings").json()
    # Spot-check the four sections the modal surfaces.
    for key in (
        "provider", "model", "vision_provider", "vision_model",
        "enable_edge_tts", "edge_tts_voice",
        "enable_semantic_memory", "semantic_top_k",
        "rate_limit_capacity",
    ):
        assert key in body, f"missing field in /api/settings: {key}"

    # Secrets must NEVER appear in the public view.
    for forbidden in ("api_key", "openai_api_key", "anthropic_api_key", "minimax_api_key"):
        assert forbidden not in body, f"{forbidden} leaked into /api/settings"


def test_put_persists_and_applies_in_place(client, tmp_path) -> None:
    from app.config import settings as live_settings
    from app.services import settings_store as store_module

    resp = client.put("/api/settings", json={"semantic_top_k": 7, "enable_edge_tts": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["semantic_top_k"] == 7
    assert body["enable_edge_tts"] is False

    # Live settings instance was mutated.
    assert live_settings.semantic_top_k == 7
    assert live_settings.enable_edge_tts is False

    # Overlay JSON was written and contains exactly the changed fields.
    overlay_path: Path = store_module.store.path
    assert overlay_path.exists()
    import json
    persisted = json.loads(overlay_path.read_text(encoding="utf-8"))
    assert persisted["semantic_top_k"] == 7
    assert persisted["enable_edge_tts"] is False


def test_put_rejects_invalid_provider(client) -> None:
    from app.config import settings as live_settings

    before = live_settings.provider
    resp = client.put("/api/settings", json={"provider": "not-a-real-provider"})
    assert resp.status_code == 400
    body = resp.json()
    assert "Invalid settings patch" in body["detail"]
    # And the live settings must not have been mutated.
    assert live_settings.provider == before


def test_put_ignores_secret_fields(client) -> None:
    """A patch that tries to set ``api_key`` must be silently dropped — the
    field is outside the MUTABLE_FIELDS allow-list."""
    from app.config import settings as live_settings

    before = live_settings.api_key
    resp = client.put("/api/settings", json={"api_key": "stolen-attempt"})
    assert resp.status_code == 200  # silently ignored, not 400
    assert live_settings.api_key == before


def test_put_empty_body_is_noop(client) -> None:
    resp = client.put("/api/settings", json={})
    assert resp.status_code == 200
    # Should match a fresh GET — nothing changed.
    fresh = client.get("/api/settings").json()
    assert resp.json() == fresh


def test_providers_endpoint_lists_all_five(client) -> None:
    body = client.get("/api/settings/providers").json()
    ids = [p["id"] for p in body["providers"]]
    for expected in ("minimax", "modelscope", "openai", "anthropic", "vllm"):
        assert expected in ids
    # ``current`` and ``current_vision`` must be in the catalog.
    assert body["current"] in ids
    assert body["current_vision"] in ids
    # vLLM doesn't support vision via this catalog; verify the flag.
    vllm = next(p for p in body["providers"] if p["id"] == "vllm")
    assert vllm["supports_vision"] is False
