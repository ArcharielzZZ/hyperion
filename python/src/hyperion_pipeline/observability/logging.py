"""Structured logging (structlog) — ingestion metrics via bound loggers."""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(json_logs: bool = False) -> None:
    """
    Configure structlog + stdlib once per process.

    ``json_logs=True`` is recommended for staging/production aggregators.
    """

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        timestamper,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
    ]
    if json_logs:
        shared.append(structlog.processors.format_exc_info)
        formatter = structlog.processors.JSONRenderer()
    else:
        shared.append(structlog.dev.set_exc_info)
        formatter = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=shared
        + [
            structlog.processors.UnicodeDecoder(),
            formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=logging.INFO, format="%(message)s")
