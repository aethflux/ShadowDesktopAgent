"""ModelClient HTTP layer: retries, pooling, error surfacing.

These tests use ``httpx.MockTransport`` to drive the real ``httpx.AsyncClient``
that ``ModelClient`` shares — so we exercise the actual retry path, not a stub.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.services import model_client


@pytest.fixture(autouse=True)
def _reset_client_state(monkeypatch):
    """Each test gets a fresh shared httpx client wired to a MockTransport."""
    asyncio.run(model_client.close_http_client())
    yield
    asyncio.run(model_client.close_http_client())


def _install_mock(handler):
    """Replace the module-level shared client with one that uses MockTransport."""
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, timeout=5.0)
    model_client._HTTP_CLIENT = client
    return client


def test_retries_then_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(model_client.settings, "model_max_retries", 3)
    monkeypatch.setattr(model_client.settings, "model_retry_backoff_seconds", 0.0)

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"ok": True})

    _install_mock(handler)
    client = model_client.ModelClient()
    result = asyncio.run(client._post("https://test/x", {}, {}))
    assert result == {"ok": True}
    assert calls["n"] == 3, "should have retried until success"


def test_non_retryable_status_raises_immediately(monkeypatch) -> None:
    monkeypatch.setattr(model_client.settings, "model_max_retries", 5)
    monkeypatch.setattr(model_client.settings, "model_retry_backoff_seconds", 0.0)

    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text="unauthorized")

    _install_mock(handler)
    client = model_client.ModelClient()
    with pytest.raises(RuntimeError, match="401"):
        asyncio.run(client._post("https://test/x", {}, {}))
    assert calls["n"] == 1, "auth errors should not be retried"


def test_exhausts_retries_and_raises(monkeypatch) -> None:
    monkeypatch.setattr(model_client.settings, "model_max_retries", 2)
    monkeypatch.setattr(model_client.settings, "model_retry_backoff_seconds", 0.0)

    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, text="boom")

    _install_mock(handler)
    client = model_client.ModelClient()
    with pytest.raises(RuntimeError, match="500"):
        asyncio.run(client._post("https://test/x", {}, {}))
    assert calls["n"] == 2


def test_get_http_client_returns_pooled_singleton() -> None:
    """Two awaits should return the same client instance (proves pooling)."""

    async def _both() -> tuple[httpx.AsyncClient, httpx.AsyncClient]:
        a = await model_client.get_http_client()
        b = await model_client.get_http_client()
        return a, b

    a, b = asyncio.run(_both())
    assert a is b
