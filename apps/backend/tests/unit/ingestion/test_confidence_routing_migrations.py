from ingestion.confidence_routing_migrations import CONFIDENCE_ROUTING_MIGRATIONS
from ingestion.confidence_routing_repository import routing_decision_uuid


def test_routing_identity_changes_with_catalog_and_policy_versions() -> None:
    base = {
        "source_system": "Transportstyrelsen",
        "source_batch_id": "batch-1",
        "source_table": "staging.transportstyrelsen_raw",
        "source_record_id": 42,
    }
    first = routing_decision_uuid(
        **base,
        candidate_catalog_version="tecdoc-2026-08",
        policy_version="routing-v1",
    )
    catalog_changed = routing_decision_uuid(
        **base,
        candidate_catalog_version="tecdoc-2026-09",
        policy_version="routing-v1",
    )
    policy_changed = routing_decision_uuid(
        **base,
        candidate_catalog_version="tecdoc-2026-08",
        policy_version="routing-v2",
    )

    assert len({first, catalog_changed, policy_changed}) == 3


def test_migration_contains_routing_and_payload_safety_contracts() -> None:
    statements = dict(CONFIDENCE_ROUTING_MIGRATIONS)
    create = statements["create_match_routing_decisions_table"]

    assert "route IN ('resolved', 'provisional', 'review_required')" in create
    assert "confidence BETWEEN 0 AND 1" in create
    assert "jsonb_typeof(decision_payload->'decision_trace') = 'array'" in create
    assert "jsonb_typeof(decision_payload->'alternative_candidates') = 'array'" in create
    assert "match_routing_selected_candidate_check" in create
    assert "selected_candidate_reference IS NOT NULL" in create
    assert "match_routing_source_version_key" in create

    heads = statements["create_match_decision_heads_table"]
    supersessions = statements["create_match_decision_supersessions_table"]
    assert "PRIMARY KEY (source_system, source_version, source_entity_key)" in heads
    assert "decision_id UUID NOT NULL UNIQUE" in heads
    assert "predecessor_decision_id UUID PRIMARY KEY" in supersessions
    assert "CHECK (predecessor_decision_id <> successor_decision_id)" in supersessions
