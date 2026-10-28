"""Structured JSON logs: one line per event, searchable by request and user.

`log_event("chat.completed", request_id=..., user_id=...)` writes a single JSON object, so
the container logs can be filtered with any log tool without parsing free text.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

LOGGER_NAME = "getnet"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)  # pyright: ignore[reportUnknownArgumentType]  # extra dict
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers = [handler]
    logger.setLevel(level.upper())
    logger.propagate = False


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def log_event(event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    get_logger().log(level, event, extra={"fields": fields})
