from ingestion.match_chunk_migrations import MATCH_CHUNK_MIGRATIONS


def test_chunk_identity_is_build_scoped_and_deterministic() -> None:
    statements = dict(MATCH_CHUNK_MIGRATIONS)
    chunks = statements["create_match_chunks_table"]

    assert "chunk_id UUID PRIMARY KEY" in chunks
    assert "UNIQUE (build_id, signature_key)" in chunks
    assert "REFERENCES core.match_chunk_builds(build_id)" in chunks
    assert "jsonb_typeof(signature) = 'object'" in chunks
    assert "signature_key ~ '^[0-9a-f]{64}$'" in chunks


def test_members_carry_no_sensitive_identifiers() -> None:
    members = dict(MATCH_CHUNK_MIGRATIONS)["create_match_chunk_members_table"]

    assert "PRIMARY KEY (chunk_id, source_record_id)" in members
    assert "vin" not in members.lower()
    assert "plate" not in members.lower()


def test_oem_evidence_is_append_only_and_billed_once_per_vin() -> None:
    statements = dict(MATCH_CHUNK_MIGRATIONS)
    evidence = statements["create_oem_vin_evidence_table"]
    trigger = statements["create_oem_vin_evidence_immutability_trigger"]

    assert "request_id UUID NOT NULL UNIQUE" in evidence
    assert "UNIQUE (provider, vin, dataset_version)" in evidence
    assert evidence.strip().startswith("CREATE TABLE IF NOT EXISTS staging.")
    assert "BEFORE UPDATE OR DELETE ON staging.oem_vin_evidence" in trigger


def test_proposals_enforce_target_and_review_state() -> None:
    proposals = dict(MATCH_CHUNK_MIGRATIONS)["create_match_chunk_proposals_table"]

    assert "proposal_source IN ('heuristic', 'agent', 'human')" in proposals
    assert "match_chunk_proposals_target_required" in proposals
    assert "match_chunk_proposals_review_state" in proposals
    assert "confidence BETWEEN 0 AND 1" in proposals


def test_migration_names_are_unique() -> None:
    names = [name for name, _ in MATCH_CHUNK_MIGRATIONS]
    assert len(names) == len(set(names))
