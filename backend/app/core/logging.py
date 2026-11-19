import json
import logging
from datetime import datetime, timezone
from typing import Any


class StructuredFormatter(logging.Formatter):
    _context_fields = (
        "event",
        "model",
        "status_code",
        "error_type",
        "function_call_count",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in self._context_fields:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        return json.dumps(payload, ensure_ascii=True)


def configure_logging() -> None:
    root_logger = logging.getLogger()
    if not any(getattr(handler, "_ecommerce_structured", False) for handler in root_logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredFormatter())
        handler._ecommerce_structured = True
        root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
