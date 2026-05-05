"""Per-request streaming context.

Tools running deep inside an agent loop occasionally need to talk back to the
SSE stream — most importantly the permission broker, which has to surface a
``permission_request`` event and await the user's decision. Threading the
``progress_cb`` through every tool's ``arun`` signature would be intrusive and
would not survive the synchronous boundary inside terminal/cli tools, so we
instead expose it via :mod:`contextvars`.

ContextVar values are copied per-task by ``asyncio.create_task``, so two
concurrent ``/api/chat/stream`` requests have independent callbacks even
though they share this module-level binding.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Awaitable, Callable

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]

# Set by ``stream_chat`` while the agent task is running. ``None`` means the
# current call is not part of a streaming session (e.g. a one-shot ``/api/chat``
# request) — in that case the broker falls back to a deny-without-prompt.
progress_cb_var: ContextVar[ProgressCallback | None] = ContextVar(
    "progress_cb", default=None,
)

# The session_id of the currently running request. Used by the permission
# broker so "remember for this session" decisions stay scoped to one chat
# session instead of leaking across users.
session_id_var: ContextVar[str] = ContextVar("session_id", default="default")
