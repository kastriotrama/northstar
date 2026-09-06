from pathlib import Path


def test_job_bookkeeping_document_covers_required_contract() -> None:
    content = Path("docs/job-bookkeeping-design.md").read_text()

    for required in (
        "core.ingest_job_runs",
        "(job_name, batch_id)",
        "records_processed",
        "records_succeeded",
        "records_failed",
        "error_code",
        "error_summary",
        "started_at",
        "finished_at",
        "completed pair returns `should_execute = false`",
        "failed pair may be reset",
        "migrate-job-bookkeeping",
    ):
        assert required in content
