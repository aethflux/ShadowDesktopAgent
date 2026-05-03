"""Centralized logging configuration for the agent core.

A single ``get_logger(name)`` helper so we never sprinkle bare ``print`` calls
or silent ``except Exception: pass`` across the codebase. All modules pull
their logger from here, which guarantees one root configuration and one
stream format.

Log level is read from the ``HOSHINO_LOG_LEVEL`` env var (default ``INFO``)
so operators can crank it to ``DEBUG`` without code changes.
"""
from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False


def _configure_root() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level_name = os.environ.get("HOSHINO_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root = logging.getLogger("hoshino")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger under the ``hoshino`` root."""
    _configure_root()
    if name.startswith("hoshino."):
        return logging.getLogger(name)
    return logging.getLogger(f"hoshino.{name}")
