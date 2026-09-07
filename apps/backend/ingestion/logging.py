import json
import logging
from datetime import UTC, datetime
from typing import Any

RESERVED_LOG_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        extra_fields = {
            key: value
            for key, value in record.__dict__.items()
            if key not in RESERVED_LOG_RECORD_FIELDS and not key.startswith("_")
        }
        payload.update(extra_fields)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, sort_keys=True)


def configure_logging(log_level: str, service_name: str = "northstar-ingestion") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level.upper())

    logging.getLogger(__name__).debug(
        "Configured structured logging",
        extra={"service": service_name},
    )
