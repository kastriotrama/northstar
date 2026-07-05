import json
import logging

from ingestion.logging import JsonFormatter


def test_json_formatter_outputs_structured_log_payload() -> None:
    record = logging.LogRecord(
        name="ingestion.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="loaded %s",
        args=("records",),
        exc_info=None,
    )

    formatted = JsonFormatter().format(record)
    payload = json.loads(formatted)

    assert payload["level"] == "INFO"
    assert payload["logger"] == "ingestion.test"
    assert payload["message"] == "loaded records"
    assert "timestamp" in payload

