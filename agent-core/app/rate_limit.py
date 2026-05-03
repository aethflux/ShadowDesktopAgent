"""Lightweight in-process rate limiting.

A token-bucket per client identity (IP). Defense in depth: the desktop app
is normally the only caller, but if the port is ever exposed an unbounded
loop on /api/chat would burn LLM tokens fast. This middleware enforces a
sane ceiling without pulling in Redis or slowapi.

Whitelist any path under ``_EXEMPT_PREFIXES`` so static asset and health
probes never trip the limiter.
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

_EXEMPT_PREFIXES = ("/artifacts/", "/health", "/api/ready")


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


class TokenBucketRateLimiter(BaseHTTPMiddleware):
    """A request-rate limiter using the token-bucket algorithm.

    ``capacity`` tokens fill at ``refill_per_second``. Each request consumes
    one token. Requests over the budget get a 429 with ``Retry-After``.
    """

    def __init__(
        self,
        app,
        *,
        capacity: int = 30,
        refill_per_second: float = 0.5,
    ) -> None:
        super().__init__(app)
        self.capacity = max(1, capacity)
        self.refill_per_second = max(0.01, refill_per_second)
        self._buckets: dict[str, _Bucket] = {}

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable],
    ):
        path = request.url.path
        if any(path.startswith(prefix) for prefix in _EXEMPT_PREFIXES):
            return await call_next(request)

        client_id = self._client_identity(request)
        if not self._consume(client_id):
            retry_after = max(1, int(1.0 / self.refill_per_second))
            return JSONResponse(
                {"detail": "Rate limit exceeded. Slow down and retry."},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)

    @staticmethod
    def _client_identity(request: Request) -> str:
        # ``request.client`` is None when the connection has no peer info
        # (e.g. test transports). Fall back to a stable sentinel so the
        # bucket stays consistent for those callers.
        if request.client and request.client.host:
            return request.client.host
        return "anonymous"

    def _consume(self, client_id: str) -> bool:
        now = time.monotonic()
        bucket = self._buckets.get(client_id)
        if bucket is None:
            bucket = _Bucket(tokens=float(self.capacity), last_refill=now)
            self._buckets[client_id] = bucket

        elapsed = now - bucket.last_refill
        bucket.tokens = min(
            float(self.capacity),
            bucket.tokens + elapsed * self.refill_per_second,
        )
        bucket.last_refill = now

        if bucket.tokens < 1.0:
            return False
        bucket.tokens -= 1.0
        return True
