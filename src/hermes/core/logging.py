"""
Structured logging setup using structlog.

Outputs JSON-formatted logs to stdout (or file) with consistent fields:
- timestamp (ISO 8601)
- level
- event
- logger
- caller
- plus any structured kwargs passed at log call site
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import structlog


def setup_logging(
    level: str = "INFO",
    format: str = "json",
    output: str = "stdout",
    file_path: str | None = None,
) -> None:
    """
    Initialize structured logging.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        format: 'json' for production, 'text' for dev (pretty console)
        output: 'stdout' or 'file'
        file_path: Required if output='file'
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Shared processors for structlog
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.CallsiteParameterAdder(
            parameters=[
                structlog.processors.CallsiteParameter.MODULE,
                structlog.processors.CallsiteParameter.FUNC_NAME,
                structlog.processors.CallsiteParameter.LINENO,
            ]
        ),
    ]

    if format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(file=_get_log_stream(output, file_path)),
        cache_logger_on_first_use=True,
    )

    # Also configure stdlib logging so libraries (redis, duckdb, etc.) log through us
    logging.basicConfig(
        level=log_level,
        stream=_get_log_stream(output, file_path),
        format="%(message)s",
        force=True,
    )

    # Redirect stdlib logging into structlog
    for name in ["redis", "duckdb", "httpx", "urllib3"]:
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True

    log = structlog.get_logger(__name__)
    log.info("logging_initialized", level=level, format=format, output=output)


def _get_log_stream(output: str, file_path: str | None):
    if output == "file":
        if not file_path:
            raise ValueError("file_path required when output='file'")
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.open("a", encoding="utf-8")
    return sys.stdout


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    """Get a structured logger. Use in any module."""
    return structlog.get_logger(name)
