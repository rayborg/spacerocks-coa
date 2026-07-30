from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

_ALLOWED_FIELDS = frozenset({"event", "method", "path", "status", "duration_ms", "request_id"})


class SafeJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        supplied = getattr(record, "safe_fields", {})
        fields = {key: supplied[key] for key in _ALLOWED_FIELDS if key in supplied}
        fields["timestamp"] = datetime.now(UTC).isoformat()
        fields["level"] = record.levelname.lower()
        return json.dumps(fields, sort_keys=True, separators=(",", ":"))


def configure_safe_logging(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("timestamp_service")
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(SafeJsonFormatter())
        logger.addHandler(handler)
    logger.propagate = False
    return logger


def safe_log(logger: logging.Logger, level: int, **fields: Any) -> None:
    logger.log(level, "request", extra={"safe_fields": fields})
