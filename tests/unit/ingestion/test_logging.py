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


def test_json_formatter_includes_extra_fields() -> None:
    record = logging.LogRecord(
        name="ingestion.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="job started",
        args=(),
        exc_info=None,
    )
    record.job_name = "tecdoc"
    record.batch_id = "batch-1"

    formatted = JsonFormatter().format(record)
    payload = json.loads(formatted)

    assert payload["job_name"] == "tecdoc"
    assert payload["batch_id"] == "batch-1"
