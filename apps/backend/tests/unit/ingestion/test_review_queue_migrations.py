from ingestion.review_queue_migrations import (
    REVIEW_QUEUE_MIGRATION_STATEMENTS,
    REVIEW_QUEUE_TABLE,
)


def test_every_statement_is_idempotent() -> None:
    for statement in REVIEW_QUEUE_MIGRATION_STATEMENTS:
        assert "IF NOT EXISTS" in statement.sql


def test_statement_names_are_unique() -> None:
    names = [statement.name for statement in REVIEW_QUEUE_MIGRATION_STATEMENTS]
    assert len(names) == len(set(names))


def test_table_statement_covers_required_queue_contract() -> None:
    table_sql = next(
        statement.sql
        for statement in REVIEW_QUEUE_MIGRATION_STATEMENTS
        if statement.name == "create_review_queue_table"
    )
    for fragment in (
        "review_id UUID NOT NULL",
        "source_system TEXT NOT NULL",
        "source_table TEXT NOT NULL",
        "source_record_id BIGINT NOT NULL",
        "reason_code TEXT NOT NULL",
        "candidate_matches JSONB NOT NULL",
        "confidence DOUBLE PRECISION",
        "status TEXT NOT NULL DEFAULT 'pending'",
        "resolution JSONB NOT NULL",
        "created_at TIMESTAMPTZ NOT NULL",
        "updated_at TIMESTAMPTZ NOT NULL",
        "resolved_at TIMESTAMPTZ",
        "CHECK (jsonb_typeof(candidate_matches) = 'array')",
        "CHECK (status IN ('pending', 'in_review', 'resolved', 'rejected'))",
        "CHECK (jsonb_typeof(resolution) = 'object')",
        "AND resolution <> '{}'::jsonb",
    ):
        assert fragment in table_sql, fragment


def test_identity_and_source_reference_are_database_constrained() -> None:
    table_sql = next(
        statement.sql
        for statement in REVIEW_QUEUE_MIGRATION_STATEMENTS
        if statement.name == "create_review_queue_table"
    )
    assert "GENERATED ALWAYS AS IDENTITY" in table_sql
    assert "UNIQUE (review_id)" in table_sql
    assert "source_table = 'staging.transportstyrelsen_raw'" in table_sql
    assert "staging\\.tecdoc_" in table_sql
    assert "CHECK (source_record_id >= 1)" in table_sql
    assert REVIEW_QUEUE_TABLE in table_sql


def test_status_and_source_indexes_support_documented_queries() -> None:
    index_sql = {
        statement.name: statement.sql
        for statement in REVIEW_QUEUE_MIGRATION_STATEMENTS
        if statement.kind == "index"
    }
    assert "(status, created_at, id)" in index_sql[
        "review_queue_status_created_at_index"
    ]
    assert "(source_table, source_record_id)" in index_sql[
        "review_queue_source_record_index"
    ]
