"""Per-path permission broker.

The broker mediates *every* path-sensitive tool call (terminal, cli, future
file-system tools) against a three-tier policy:

1. **Deny-list** — paths inside ``workspace_denylist_json`` (or any of the
   built-in defaults like ``C:\\Windows`` or ``~/.ssh``) are unconditionally
   rejected. Even an explicit user "allow always" cannot bypass these.
2. **Allow-list** — paths inside ``workspace_allowlist_json`` (or the legacy
   ``command_workspace_root``) pass without prompting.
3. **Ask the user** — anything else opens a ``permission_request`` SSE event
   on the active streaming session and waits for the user's choice. Choices
   include "this once", "this session", "always" (persisted to the allowlist),
   or "deny".

When called outside a streaming session (no ``progress_cb`` in the context),
the broker falls back to deny-without-prompt — there's nobody to ask, so we
keep the legacy hard-deny behaviour for ``/api/chat`` (non-stream).
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Literal

from app.config import settings
from app.logging import get_logger
from app.services.streaming_context import progress_cb_var, session_id_var

logger = get_logger("services.permission_broker")

DecisionLiteral = Literal["allow_once", "allow_session", "allow_always", "deny"]


def _normalize(path: Path | str) -> Path:
    """Resolve and canonicalize a path so prefix comparisons are reliable.

    ``expanduser`` swaps ``~`` for the current user's home; ``resolve`` makes
    it absolute and normalizes separators. We deliberately don't follow
    symlinks beyond what ``resolve`` already does — the goal is to compare
    against the user's intent, not to defeat a determined attacker.
    """
    return Path(path).expanduser().resolve()


def _path_prefix_match(target: Path, candidate: Path) -> bool:
    """True when ``target`` is ``candidate`` itself or below it.

    We compare the resolved string forms because ``Path.is_relative_to``
    requires Python 3.9+ semantics that mishandle Windows drive letters with
    different cases. Lower-case both sides on Windows; keep case sensitivity
    on POSIX.
    """
    target_str = str(target)
    candidate_str = str(candidate)
    if os.name == "nt":
        target_str = target_str.lower()
        candidate_str = candidate_str.lower()
    if target_str == candidate_str:
        return True
    sep = os.sep
    return target_str.startswith(candidate_str + sep)


def _parse_path_list(raw: str) -> list[Path]:
    """Parse a JSON array of path strings into resolved Path objects.

    Invalid JSON or non-list payloads degrade to an empty list rather than
    raising — bad config should never crash a tool invocation. We log a
    warning so it's visible during debugging.
    """
    if not raw or not raw.strip():
        return []
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Could not parse path list %r: %s", raw[:80], exc)
        return []
    if not isinstance(items, list):
        return []
    out: list[Path] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            continue
        try:
            out.append(_normalize(item))
        except (OSError, RuntimeError):
            continue
    return out


class PermissionDenied(PermissionError):
    """Raised when the broker decides (or the user decides) to reject a path.

    Inherits from :class:`PermissionError` so existing tool error-handling
    paths that catch ``PermissionError`` keep working.
    """


class PermissionBroker:
    """Singleton-ish coordinator for path approval requests.

    Thread-safety: the pending-future map is mutated from both the agent task
    (``check``) and the FastAPI request handler that calls ``resolve``. We
    guard access with an :class:`asyncio.Lock` plus a :class:`threading.Lock`
    for the rare case where ``resolve`` is invoked from a non-async caller.
    """

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[DecisionLiteral]] = {}
        self._session_allowed: dict[str, set[str]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Public API used by tools                                           #
    # ------------------------------------------------------------------ #

    async def check(
        self,
        path: Path | str,
        *,
        reason: str,
        tool_name: str | None = None,
    ) -> None:
        """Block until the broker is satisfied that ``path`` may be accessed.

        Raises :class:`PermissionDenied` if a deny-list rule matches, the
        user explicitly denies, or no streaming session is available to ask.
        Returns ``None`` on success — callers don't need a return value, the
        absence of an exception is the green light.
        """
        target = _normalize(path)
        target_str = str(target)
        progress_cb = progress_cb_var.get()
        session_id = session_id_var.get()

        # Deny-list always wins — even over an explicit allow-list entry.
        for deny in self._effective_denylist():
            if _path_prefix_match(target, deny):
                raise PermissionDenied(
                    f"path is in deny-list ({deny}): {target}"
                )

        # Allow-list (persisted across runs).
        for allow in self._effective_allowlist():
            if _path_prefix_match(target, allow):
                return

        # Per-session "remember this session" approvals.
        with self._lock:
            session_allowed = self._session_allowed.get(session_id, set())
        if any(_path_prefix_match(target, _normalize(p)) for p in session_allowed):
            return

        # Confirmation disabled → fall back to legacy hard-deny so we don't
        # silently expand permissions on environments that explicitly opted out.
        if not settings.require_path_confirmation:
            raise PermissionDenied(
                f"path is outside the allowlist and confirmation is disabled: {target}"
            )

        # No streaming session → there's nobody to ask. Hard-deny so non-stream
        # /api/chat behaves predictably.
        if progress_cb is None:
            raise PermissionDenied(
                f"path requires user approval but no streaming session is active: {target}"
            )

        # Surface a permission_request and await the user's decision.
        request_id = uuid.uuid4().hex[:12]
        future: asyncio.Future[DecisionLiteral] = (
            asyncio.get_event_loop().create_future()
        )
        with self._lock:
            self._pending[request_id] = future

        await progress_cb(
            {
                "event": "permission_request",
                "data": {
                    "request_id": request_id,
                    "path": target_str,
                    "reason": reason,
                    "tool_name": tool_name,
                    "session_id": session_id,
                    "timeout_seconds": settings.permission_request_timeout_seconds,
                },
            }
        )

        try:
            decision = await asyncio.wait_for(
                future, timeout=settings.permission_request_timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            with self._lock:
                self._pending.pop(request_id, None)
            raise PermissionDenied(
                f"permission request timed out after "
                f"{settings.permission_request_timeout_seconds}s: {target}"
            ) from exc

        if decision == "allow_once":
            return
        if decision == "allow_session":
            with self._lock:
                self._session_allowed.setdefault(session_id, set()).add(target_str)
            return
        if decision == "allow_always":
            self._persist_allow(target_str)
            return
        # decision == "deny"
        raise PermissionDenied(f"user denied access to: {target}")

    # ------------------------------------------------------------------ #
    # Public API used by HTTP handler                                    #
    # ------------------------------------------------------------------ #

    def resolve(self, request_id: str, decision: DecisionLiteral) -> bool:
        """Settle a pending request. Returns True if the request existed.

        The future may already be done if ``check`` timed out; in that case
        we silently drop the late decision — the tool already errored out.
        """
        with self._lock:
            future = self._pending.pop(request_id, None)
        if future is None:
            return False
        if future.done():
            return False
        loop = future.get_loop()
        loop.call_soon_threadsafe(future.set_result, decision)
        return True

    def pending_count(self) -> int:
        """Number of outstanding requests — useful for diagnostics."""
        with self._lock:
            return len(self._pending)

    def clear_session(self, session_id: str) -> None:
        """Drop the per-session allow cache (e.g. when a session is reset)."""
        with self._lock:
            self._session_allowed.pop(session_id, None)

    # ------------------------------------------------------------------ #
    # Internals                                                          #
    # ------------------------------------------------------------------ #

    def _effective_allowlist(self) -> list[Path]:
        explicit = _parse_path_list(settings.workspace_allowlist_json)
        # The legacy ``command_workspace_root`` is always trusted — that's the
        # project itself, the point of the whole tool. We append it last so
        # explicit entries take precedence in user-visible diagnostics.
        try:
            explicit.append(_normalize(settings.command_workspace_root))
        except (OSError, RuntimeError):
            pass
        return explicit

    def _effective_denylist(self) -> list[Path]:
        return _parse_path_list(settings.workspace_denylist_json)

    def _persist_allow(self, path_str: str) -> None:
        """Add ``path_str`` to the persistent allowlist via SettingsStore."""
        # Imported here to avoid a circular import at module load time
        # (settings_store → config → broker through the agent stack).
        from app.services.settings_store import store  # noqa: WPS433

        try:
            current_raw = settings.workspace_allowlist_json or "[]"
            current = json.loads(current_raw)
            if not isinstance(current, list):
                current = []
        except json.JSONDecodeError:
            current = []
        if path_str in current:
            return
        current.append(path_str)
        new_json = json.dumps(current, ensure_ascii=False)
        try:
            store.update({"workspace_allowlist_json": new_json}, settings)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("Could not persist allow-always for %s: %s", path_str, exc)


# Module-level singleton — wired through ContextVar.
broker = PermissionBroker()
