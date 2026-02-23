"""
app/core/logging.py

Structured logging setup using structlog.

- In production: outputs newline-delimited JSON (parseable by Railway/Render/Datadog).
- In development: outputs coloured, human-readable console lines with timestamps.

Usage:
    from app.core.logging import get_logger
    logger = get_logger(__name__)
    logger.info("submission_received", submission_id=str(submission_id))

Never use print() anywhere in the application — always use a logger.
"""

import logging
import sys

import structlog
from structlog.types import EventDict, Processor


def _drop_color_message_key(_, __, event_dict: EventDict) -> EventDict:
    """Remove the `color_message` key injected by uvicorn's ColourizedFormatter
    when structlog intercepts its log records — avoids duplication in JSON output."""
    event_dict.pop("color_message", None)
    return event_dict


def setup_logging(environment: str = "development") -> None:
    """Configure structlog and stdlib logging.

    Call this once during application startup (inside the lifespan handler).

    Args:
        environment: "development" | "production". Determines output format.
    """
    is_production = environment == "production"

    shared_processors: list[Processor] = [
        # Inject the log level (DEBUG, INFO, …) into the event dict.
        structlog.stdlib.add_log_level,
        # Add ISO-8601 timestamp.
        structlog.processors.TimeStamper(fmt="iso"),
        # Render exception tracebacks if present.
        structlog.processors.StackInfoRenderer(),
        _drop_color_message_key,
    ]

    if is_production:
        # JSON output — one object per line, parseable by log aggregators.
        processors: list[Processor] = shared_processors + [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Human-readable coloured output for local development.
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Also route stdlib logging (used by uvicorn, supabase-py, httpx, etc.)
    # through structlog so everything ends up in the same format.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog bound logger for the given module name.

    Usage:
        logger = get_logger(__name__)
        logger.info("event_name", key="value")
    """
    return structlog.get_logger(name)
