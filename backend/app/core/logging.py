"""Structured JSON logging.

Emits one JSON object per log line. Standard fields: `timestamp`,
`level`, `logger`, `message`. Extra context can be attached via
`logger.info("...", extra={"key": "value"})`.
"""

from __future__ import annotations

import logging
import sys

from pythonjsonlogger import jsonlogger


class _JsonFormatter(jsonlogger.JsonFormatter):
    """JSON formatter that always renames `asctime` → `timestamp`."""

    def add_fields(
        self,
        log_record: dict[str, object],
        record: logging.LogRecord,
        message_dict: dict[str, object],
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        if "asctime" in log_record:
            log_record["timestamp"] = log_record.pop("asctime")
        log_record.setdefault("level", record.levelname)
        log_record.setdefault("logger", record.name)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON handler on the root logger.

    Idempotent: running twice replaces the handler instead of appending,
    so test fixtures and FastAPI workers don't accumulate duplicates.
    """
    root = logging.getLogger()
    root.setLevel(level.upper())

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)

    # Hush the noisier infrastructure loggers without losing warnings.
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
