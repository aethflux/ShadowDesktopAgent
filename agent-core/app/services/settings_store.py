"""User-mutable settings overlay.

The base configuration in ``app.config.Settings`` mixes immutable infrastructure
(secrets, paths, allowlists) with preferences the user is allowed to flip at
runtime (which LLM provider to use, which voice to speak with, whether to
enable semantic memory, etc.). This module persists *just* the mutable
preferences to a JSON file under ``memory/`` and applies them on top of the
in-memory ``settings`` instance both at startup and on every PUT.

Why a JSON overlay instead of touching ``.env``?

- ``.env`` should hold secrets only. The user shouldn't have to re-enter their
  API key every time they want to flip a checkbox.
- A separate file means we never have to parse / rewrite the user's hand-edited
  ``.env`` (which would be lossy with respect to comments and ordering).
- The overlay file is human-readable and easy to back up.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.config import Settings, settings
from app.logging import get_logger

logger = get_logger("services.settings_store")


# Whitelist of fields the user is allowed to mutate at runtime.
# Anything outside this list is rejected by ``apply_patch`` so a malicious or
# buggy client can't reach into ``api_key`` or ``memory_dir``.
MUTABLE_FIELDS: frozenset[str] = frozenset(
    {
        # LLM
        "provider",
        "model",
        "openai_model",
        "anthropic_model",
        "vllm_model",
        "minimax_model",
        "modelscope_model",
        "vision_provider",
        "vision_model",
        "enable_prompt_cache",
        # Reliability
        "model_max_retries",
        "model_retry_backoff_seconds",
        "anthropic_max_tokens",
        # Memory
        "enable_semantic_memory",
        "semantic_top_k",
        # Voice
        "enable_edge_tts",
        "edge_tts_voice",
        "edge_tts_rate",
        "edge_tts_pitch",
        "enable_minimax_voice",
        "minimax_tts_voice_id",
        "minimax_tts_model",
        "minimax_tts_speed",
        "minimax_tts_pitch",
        "minimax_tts_volume",
        "enable_modelscope_tts",
        "modelscope_tts_instruction",
        "enable_gemini_tts",
        "gemini_tts_voice",
        "gemini_tts_speed",
        # Behavior
        "rate_limit_capacity",
        "rate_limit_refill_per_second",
        "tts_audio_retention_hours",
        "enable_gui_automation",
        # Permission broker — workspace_*_json are JSON arrays of paths.
        "workspace_allowlist_json",
        "workspace_denylist_json",
        "require_path_confirmation",
        "permission_request_timeout_seconds",
        # Persona — single JSON blob containing the full PersonaConfig.
        "persona_config_json",
    }
)


class SettingsStore:
    """JSON-backed overlay applied on top of ``app.config.settings``."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    # ---- raw IO ---------------------------------------------------------- #

    def load_raw(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            with self._lock:
                return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not parse settings overlay at %s: %s", self.path, exc)
            return {}

    def save_raw(self, data: dict[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )

    # ---- apply / update -------------------------------------------------- #

    def apply_to(self, target: Settings) -> dict[str, Any]:
        """Apply persisted overrides to a Settings instance. Returns the
        applied subset (only fields that were valid)."""
        data = self.load_raw()
        applied: dict[str, Any] = {}
        for key, value in data.items():
            if key not in MUTABLE_FIELDS:
                continue
            if not hasattr(target, key):
                continue
            try:
                setattr(target, key, value)
                applied[key] = value
            except (TypeError, ValueError, ValidationError) as exc:
                logger.warning("Skipping invalid settings overlay '%s'=%r: %s", key, value, exc)
        if applied:
            logger.info("Applied %d settings overlay value(s)", len(applied))
        return applied

    def update(self, patch: dict[str, Any], target: Settings) -> dict[str, Any]:
        """Validate a patch, persist it merged with existing overrides, and
        apply it to ``target``. Returns the new merged overlay dict.

        Unknown fields are silently dropped; type errors raise ``ValueError``.
        """
        cleaned: dict[str, Any] = {}
        for key, value in patch.items():
            if key not in MUTABLE_FIELDS:
                logger.debug("Ignoring non-mutable settings field '%s'", key)
                continue
            if not hasattr(target, key):
                continue
            cleaned[key] = value

        # Try setting on the live instance first — with ``validate_assignment``
        # in the Settings model_config, Pydantic raises ValidationError on bad
        # values. Only persist on success.
        snapshot = {k: getattr(target, k) for k in cleaned}
        try:
            for key, value in cleaned.items():
                setattr(target, key, value)
        except (TypeError, ValueError, ValidationError) as exc:
            # Roll back partially-applied changes.
            for key, prev in snapshot.items():
                setattr(target, key, prev)
            raise ValueError(f"Invalid settings patch: {exc}") from exc

        with self._lock:
            current = self.load_raw()
            current.update(cleaned)
            self.save_raw(current)
        logger.info("Persisted %d settings change(s): %s", len(cleaned), sorted(cleaned.keys()))
        return current


# Singleton — wired to the default memory dir.
store = SettingsStore(settings.memory_dir / "user_settings.json")
