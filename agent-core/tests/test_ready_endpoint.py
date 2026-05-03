"""Tests for the /api/ready diagnostics endpoint."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_ready_returns_full_diagnostics(client) -> None:
    resp = client.get("/api/ready")
    assert resp.status_code == 200
    body = resp.json()

    # Top-level shape contract — these keys are what the desktop UI reads.
    for section in ("model", "memory", "tools", "mcp", "skills", "voice"):
        assert section in body, f"missing section: {section}"

    assert body["model"]["provider"]
    assert body["model"]["name"]
    assert "vision_supported" in body["model"]

    assert isinstance(body["tools"]["count"], int)
    assert isinstance(body["tools"]["names"], list)
    assert body["tools"]["count"] == len(body["tools"]["names"])

    assert isinstance(body["mcp"]["registered"], int)
    assert isinstance(body["mcp"]["running"], int)
    assert body["mcp"]["running"] <= body["mcp"]["registered"]


def test_health_endpoint_minimal_shape(client) -> None:
    """/health stays the lightweight liveness probe — keep it minimal."""
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "provider" in body
    assert "model" in body
