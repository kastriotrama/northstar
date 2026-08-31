from ingestion.match_run_migrations import MATCH_RUN_MIGRATIONS


def test_match_run_schema_pins_inputs_and_balances_terminal_counts() -> None:
    statements = dict(MATCH_RUN_MIGRATIONS)
    runs = statements["create_match_runs_table"]

    assert "operation_id UUID PRIMARY KEY" in runs
    assert "expected_source_rows BIGINT NOT NULL" in runs
    assert "normalization_rule_version TEXT NOT NULL" in runs
    assert "candidate_catalog_version TEXT NOT NULL" in runs
    assert "policy_version TEXT NOT NULL" in runs
    assert "code_revision TEXT NOT NULL" in runs
    assert "mode IN ('dry_run', 'persist')" in runs
    assert "processed <= expected_source_rows" in runs
    assert "match_runs_accounting_balance" in runs
    assert "normalization_review + policy_excluded + failed" in runs


def test_checkpoint_identity_is_sequential_and_operation_scoped() -> None:
    checkpoints = dict(MATCH_RUN_MIGRATIONS)["create_match_run_checkpoints_table"]

    assert "PRIMARY KEY (operation_id, batch_number)" in checkpoints
    assert "UNIQUE (operation_id, last_source_record_id)" in checkpoints
    assert "REFERENCES core.match_runs(operation_id)" in checkpoints
    assert "jsonb_typeof(counters) = 'object'" in checkpoints


def test_reason_counts_are_operation_scoped_aggregates() -> None:
    reasons = dict(MATCH_RUN_MIGRATIONS)["create_match_run_reason_counts_table"]

    assert "REFERENCES core.match_runs(operation_id)" in reasons
    assert "PRIMARY KEY (operation_id, reason_code)" in reasons
    assert "occurrence_count BIGINT NOT NULL" in reasons


def test_blocker_counts_are_mutually_exclusive_operation_aggregates() -> None:
    blockers = dict(MATCH_RUN_MIGRATIONS)["create_match_run_blocker_counts_table"]

    assert "REFERENCES core.match_runs(operation_id)" in blockers
    assert "PRIMARY KEY (operation_id, blocker_category)" in blockers
    assert "occurrence_count BIGINT NOT NULL" in blockers
