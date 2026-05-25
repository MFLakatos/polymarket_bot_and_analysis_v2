"""Structured logging using structlog. Falls back to stdlib if structlog is missing."""
from __future__ import annotations

import logging
import sys
from typing import Any

try:
    import structlog
    _HAS_STRUCTLOG = True
except ImportError:
    structlog = None  # type: ignore[assignment]
    _HAS_STRUCTLOG = False


def configure_logging(level: str = "INFO", use_json: bool = False) -> None:
    """Configure root logger + structlog (idempotent)."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not _HAS_STRUCTLOG:
        return

    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if use_json:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    """Return a structlog (or stdlib) logger."""
    if _HAS_STRUCTLOG:
        return structlog.get_logger(name)
    return logging.getLogger(name)
