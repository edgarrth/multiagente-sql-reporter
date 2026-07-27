from __future__ import annotations

import hashlib
import logging
import re
import sys
from typing import Any

import structlog

from axiz.pe.sql_agent.config import Settings

_WHITESPACE = re.compile(r"\s+")


def text_fingerprint(value: str | None, *, length: int = 16) -> str | None:
    """Return a stable, non-reversible identifier for sensitive text.

    Questions, prompts and SQL should not be written to production logs by default. A
    fingerprint lets operators correlate retries and stages without exposing the content.
    """
    if not value:
        return None
    normalized = _WHITESPACE.sub(" ", value).strip()
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:length]


def sql_log_fields(sql: str | None, *, include_text: bool = False) -> dict[str, Any]:
    normalized = _WHITESPACE.sub(" ", str(sql or "")).strip()
    fields: dict[str, Any] = {
        "sql_fingerprint": text_fingerprint(normalized),
        "sql_chars": len(normalized),
    }
    if include_text and normalized:
        fields["sql"] = normalized
    return fields


def _level(value: str) -> int:
    return getattr(logging, str(value or "INFO").upper(), logging.INFO)


def configure_logging(settings: Settings) -> None:
    """Configure structured application logs and suppress noisy dependency logs.

    Uvicorn's access logger is disabled by the container command. HTTP request logs are
    emitted by our ASGI middleware instead, which lets us suppress health-check requests
    without losing useful application traffic diagnostics.
    """
    level = _level(settings.log_level)
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    if settings.log_format == "console":
        renderer: Any = structlog.dev.ConsoleRenderer(colors=False)
    else:
        renderer = structlog.processors.JSONRenderer()

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Replace noisy transport-level messages with explicit provider-aware LLM logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)

    # Uvicorn access logging is intentionally replaced by RequestLoggingMiddleware.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.disabled = True
    access_logger.propagate = False

    # Reuse the same structured formatter for server lifecycle/error messages and avoid
    # duplicate lines from Uvicorn's preconfigured handlers.
    for logger_name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.disabled = False
        uvicorn_logger.propagate = True
        uvicorn_logger.setLevel(level)

    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
