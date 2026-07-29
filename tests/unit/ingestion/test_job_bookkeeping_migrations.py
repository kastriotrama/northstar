from ingestion.job_bookkeeping_migrations import (
    JOB_BOOKKEEPING_MIGRATION_STATEMENTS,
    JOB_RUNS_TABLE,
    JOB_RUN_STATUSES,
)


def test_migration_contract_contains_table_and_worklist_index() -> None:
    statements = {statement.name: statement for statement in JOB_BOOKKEEPING_MIGRATION_STATEMENTS}

    assert JOB_RUNS_TABLE == "core.ingest_job_runs"
    assert JOB_RUN_STATUSES == ("running", "completed", "failed")
    assert "create_ingest_job_runs_table" in statements
    assert "UNIQUE (job_name, batch_id)" in statements["create_ingest_job_runs_table"].sql
    assert (
        "records_processed = records_succeeded + records_failed"
        in statements["create_ingest_job_runs_table"].sql
    )
    assert "error_code TEXT" in statements["create_ingest_job_runs_table"].sql
    assert "error_summary TEXT" in statements["create_ingest_job_runs_table"].sql
    assert "ingest_job_runs_status_started_at_index" in statements
