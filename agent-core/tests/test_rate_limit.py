"""Tests for the token-bucket rate limiter middleware."""
from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.rate_limit import TokenBucketRateLimiter


def _build_app(capacity: int, refill: float) -> FastAPI:
    app = FastAPI()
    app.add_middleware(TokenBucketRateLimiter, capacity=capacity, refill_per_second=refill)

    @app.get("/api/ping")
    def ping() -> dict:
        return {"ok": True}

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


def test_under_capacity_allows_all_requests() -> None:
    app = _build_app(capacity=5, refill=0.1)
    with TestClient(app) as client:
        for _ in range(5):
            assert client.get("/api/ping").status_code == 200


def test_over_capacity_returns_429() -> None:
    app = _build_app(capacity=2, refill=0.05)
    with TestClient(app) as client:
        client.get("/api/ping")
        client.get("/api/ping")
        resp = client.get("/api/ping")
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers


def test_health_endpoint_is_exempt() -> None:
    app = _build_app(capacity=1, refill=0.01)
    with TestClient(app) as client:
        client.get("/api/ping")
        # /api/ping is now over budget, but /health should keep responding.
        for _ in range(5):
            assert client.get("/health").status_code == 200


def test_bucket_refills_over_time() -> None:
    app = _build_app(capacity=1, refill=10.0)
    with TestClient(app) as client:
        assert client.get("/api/ping").status_code == 200
        assert client.get("/api/ping").status_code == 429
        # Wait for the bucket to refill (~0.1s at 10 tokens/s).
        time.sleep(0.2)
        assert client.get("/api/ping").status_code == 200
