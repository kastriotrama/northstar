"""The pure parts of the pilot database build: classes, predicates, plan, refusals."""

from __future__ import annotations

from dataclasses import fields, replace

import psycopg
import pytest

from api.app.features.vehicle_matching.repository import SAMPLE_SEED
from ingestion.pilot_database import (
    DEFAULT_SEED,
    DEFAULT_SIZE,
    MANIFEST_KEY,
    MATCH_IMPACT_SAMPLE_SIZE,
    PILOT_MIGRATIONS,
    PILOT_TABLES,
    Check,
    Fingerprint,
    PilotBuildError,
    PilotOptions,
    PilotOutcome,
    PilotPlan,
    PlannedTable,
    TableSpec,
    build_manifest,
    build_marker,
    check_manifest,
    check_target_name,
    chunked,
    describe_database_error,
    eligible_predicate,
    estimate_bytes,
    format_bytes,
    format_manifest_verification,
    format_plan,
    format_summary,
    parse_marker,
    selection_predicate,
    selections,
    slice_query,
    spec_by_table,
    target_refusal,
)

OPTIONS = PilotOptions(target_database="northstar_pilot", catalog_batch="batch-1")


def _spec(table: str) -> TableSpec:
    return spec_by_table()[table]


def test_defaults_are_the_pilot_the_runbook_describes() -> None:
    assert (OPTIONS.size, OPTIONS.seed, OPTIONS.scope, OPTIONS.registered_only) == (
        500_000, "northstar-match-impact-v1", "passenger", True
    )
    assert (DEFAULT_SIZE, DEFAULT_SEED) == (OPTIONS.size, OPTIONS.seed)


def test_the_default_seed_is_the_match_impact_sample_seed_itself() -> None:
    # Not a second copy of the string: the slice then starts with the report's sample.
    assert DEFAULT_SEED is SAMPLE_SEED
    assert MATCH_IMPACT_SAMPLE_SIZE == 30_000 <= DEFAULT_SIZE


def test_only_these_slice_tables_carry_a_predicate_besides_their_key() -> None:
    # A predicate on a slice table drops rows of slice cars, and the verification
    # cannot see it: source and target are selected with the same predicate. So
    # the full list is pinned here. Closed identifiers (plate history), superseded
    # field resolutions, ledger corrections and closed review items all come along.
    ts_only = "source_table = 'staging.transportstyrelsen_raw'"

    assert {spec.table: (spec.where, spec.where_options) for spec in PILOT_TABLES
            if spec.table_class == "B" and (spec.where is not None or spec.where_options)} == {
        "core.normalization_results": (ts_only, ()),
        "core.review_queue": (ts_only, ()),
    }
    assert {spec.table for spec in PILOT_TABLES if spec.table_class == "B"} == {
        "core.match_chunks", "core.match_chunk_members", "core.match_field_resolutions",
        "staging.oem_vin_evidence", "core.match_chunk_samples",
        "staging.transportstyrelsen_raw", "core.normalization_results", "core.vehicle_facts",
        "core.review_queue", "core.vehicles", "core.vehicle_identifiers",
        "core.vehicle_source_links", "core.enrichment_ledger", "core.vehicle_ktype_choices",
        "core.vehicle_fact_corrections",
    }


def test_every_table_has_one_class() -> None:
    names = [spec.table for spec in PILOT_TABLES]

    assert len(names) == len(set(names))
    assert {spec.table_class for spec in PILOT_TABLES} == {"A", "B", "C"}
    assert all(name.split(".")[0] in ("core", "staging") for name in names)


def test_the_tables_reviewers_and_rules_write_are_copied_whole() -> None:
    whole = {spec.table for spec in PILOT_TABLES
             if spec.table_class == "A" and spec.where is None}

    assert {
        "core.match_resolution_rules", "core.match_chunk_proposals",
        "core.tecdoc_resolution_rules", "core.translation_rule_versions",
        "core.translation_rule_drafts", "core.manufacturer_entity_drafts",
        "core.translation_rule_definition_versions", "core.translation_rule_definitions",
        "core.tecdoc_rule_versions", "core.tecdoc_rules", "core.vehicle_enrichment_rules",
        "core.match_review_rule_decisions", "core.match_chunk_builds",
        "core.tecdoc_identity_registry", "core.ingest_job_runs",
        # A decision covers cars in and outside any slice: all of them come along.
        "core.vehicle_correction_decisions",
    } <= whole


def test_slice_tables_are_cut_by_a_key_and_whole_tables_are_not() -> None:
    option_names = {field.name for field in fields(PilotOptions)}
    for spec in PILOT_TABLES:
        if spec.table_class == "B":
            assert spec.key_column and spec.key_set, spec.table
        else:
            assert spec.key_column is None and spec.key_set is None, spec.table
        assert (spec.where or "").count("%s") == len(spec.where_options), spec.table
        assert set(spec.where_options) <= option_names, spec.table
        assert (spec.seal_column is None) == (spec.seal_key is None), spec.table
        if spec.table_class != "C":
            assert not spec.may_hold_rows, spec.table


def test_parents_come_before_their_children_in_the_load_order() -> None:
    order = [spec.table for spec in PILOT_TABLES]
    for parent, child in (
        ("core.tecdoc_source_batches", "core.tecdoc_canonical_candidates"),
        ("core.tecdoc_rule_versions", "core.tecdoc_rules"),
        ("core.match_chunk_builds", "core.match_chunks"),
        ("core.match_chunks", "core.match_chunk_members"),
        ("core.match_chunks", "core.match_chunk_proposals"),
        ("core.match_resolution_rules", "core.match_field_resolutions"),
        ("core.vehicles", "core.vehicle_identifiers"),
        ("core.vehicles", "core.vehicle_source_links"),
        ("core.vehicles", "core.vehicle_ktype_choices"),
        ("core.vehicles", "core.vehicle_fact_corrections"),
        ("core.vehicle_correction_decisions", "core.vehicle_fact_corrections"),
        ("core.match_runs", "core.match_review_rule_decisions"),
    ):
        assert order.index(parent) < order.index(child)


def test_only_the_recounted_counters_are_left_out_of_the_content_check() -> None:
    assert {spec.table: spec.recomputed for spec in PILOT_TABLES if spec.recomputed} == {
        "core.match_chunk_builds": ("row_count", "chunk_count"),
        "core.match_chunks": ("member_count", "reason_profile"),
    }


def test_the_schema_comes_from_all_seventeen_migration_sets() -> None:
    names = [name for name, _ in PILOT_MIGRATIONS]
    assert len(names) == 17
    assert len(set(names)) == 17
    # People's KType choices reference core.vehicles: the API's lookups read the
    # table, so a pilot without it answers 503.
    assert names.index("vehicle core") < names.index("vehicle ktype choices")
    # So do their corrections, whose rows name the decision that wrote them.
    assert (
        names.index("vehicle core")
        < names.index("vehicle correction decisions")
        < names.index("vehicle fact corrections")
    )


def test_the_slice_query_is_the_seeded_sample_of_registered_cars() -> None:
    assert slice_query(registered_only=True) == (
        "SELECT vehicle_id FROM core.vehicles "
        "WHERE vehicle_scope = %s AND registry_status = 'registered' "
        "ORDER BY md5(%s || vehicle_id), vehicle_id LIMIT %s"
    )
    assert eligible_predicate(False) == "vehicle_scope = %s"
    assert "registry_status" not in slice_query(registered_only=False)


def test_a_whole_table_is_selected_without_keys() -> None:
    assert selection_predicate(_spec("core.match_resolution_rules"), OPTIONS, None) == ("TRUE", [])


def test_the_catalog_tables_are_cut_to_the_pinned_batch() -> None:
    for table in ("core.tecdoc_source_batches", "core.tecdoc_canonical_candidates",
                  "core.tecdoc_candidate_relationships"):
        assert selection_predicate(_spec(table), OPTIONS, None) == ("(batch_id = %s)", ["batch-1"])


def test_a_slice_table_is_selected_by_its_keys() -> None:
    assert selection_predicate(_spec("core.vehicles"), OPTIONS, ("NOR-A", "NOR-B")) == (
        "vehicle_id = ANY(%s::text[])", [["NOR-A", "NOR-B"]]
    )
    assert selection_predicate(_spec("staging.transportstyrelsen_raw"), OPTIONS, (7, 9)) == (
        "id = ANY(%s::bigint[])", [[7, 9]]
    )
    assert selection_predicate(_spec("core.enrichment_ledger"), OPTIONS, ("NOR-A",)) == (
        "target_node_id = ANY(%s::text[])", [["NOR-A"]]
    )


def test_tables_shared_with_other_sources_are_cut_to_ts_records() -> None:
    for table in ("core.normalization_results", "core.review_queue"):
        predicate, params = selection_predicate(_spec(table), OPTIONS, (7,))
        assert predicate == (
            "(source_table = 'staging.transportstyrelsen_raw') "
            "AND source_record_id = ANY(%s::bigint[])"
        )
        assert params == [[7]]


def test_a_slice_table_without_keys_is_refused() -> None:
    with pytest.raises(ValueError, match="needs its keys"):
        selection_predicate(_spec("core.vehicles"), OPTIONS, None)


def test_keys_are_read_in_chunks_that_cover_each_key_once() -> None:
    keys = tuple(range(1, 8))
    chosen = list(selections(_spec("staging.transportstyrelsen_raw"), OPTIONS, keys, 3))

    assert [params[0] for _, params in chosen] == [[1, 2, 3], [4, 5, 6], [7]]
    assert {predicate for predicate, _ in chosen} == {"id = ANY(%s::bigint[])"}


def test_a_table_without_an_index_on_its_key_is_read_once() -> None:
    keys = tuple(range(1, 8))
    chosen = list(selections(_spec("core.match_field_resolutions"), OPTIONS, keys, 3))

    assert [params[0] for _, params in chosen] == [list(keys)]


def test_selections_for_whole_empty_and_left_out_tables() -> None:
    assert list(selections(_spec("core.match_resolution_rules"), OPTIONS, None, 3)) == [
        ("TRUE", [])
    ]
    assert list(selections(_spec("core.vehicles"), OPTIONS, (), 3)) == []
    assert list(selections(_spec("core.match_run_checkpoints"), OPTIONS, None, 3)) == []


def test_chunked_splits_in_order_and_rejects_a_zero_size() -> None:
    assert [list(chunk) for chunk in chunked(["a", "b", "c"], 2)] == [["a", "b"], ["c"]]
    assert list(chunked([], 2)) == []
    with pytest.raises(ValueError):
        list(chunked([1], 0))


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("app", "is the source database"),
        ("postgres", "system database"),
        ("template1", "system database"),
        ("maintenance", "system database"),
        ("Northstar", "lower-case"),
        ("pilot-1", "lower-case"),
        ("1pilot", "lower-case"),
        ("", "lower-case"),
        ('pilot"; DROP DATABASE app; --', "lower-case"),
    ],
)
def test_unsafe_target_names_are_refused(target: str, message: str) -> None:
    with pytest.raises(PilotBuildError, match=message):
        check_target_name(target, source="app", maintenance="maintenance")


def test_a_plain_new_name_is_accepted() -> None:
    check_target_name("northstar_pilot_2", source="app", maintenance="postgres")


def test_the_marker_round_trips_and_other_comments_are_not_markers() -> None:
    comment = build_marker(OPTIONS, source_database="app", state="building",
                           started_at="2026-10-02T10:00:00+00:00")
    marker = parse_marker(comment)

    assert marker is not None
    assert marker["state"] == "building"
    assert marker["seed"] == OPTIONS.seed and marker["size"] == OPTIONS.size
    assert marker["catalog_batch"] == "batch-1" and marker["source_database"] == "app"
    for other in (None, "", "default administrative connection database", "[1, 2]",
                  '{"something": "else"}', '{"northstar_pilot_build": "yes"}'):
        assert parse_marker(other) is None


def test_an_existing_target_is_only_replaced_when_it_is_a_pilot_build() -> None:
    marker = {"state": "building"}

    assert target_refusal("pilot", exists=False, marker=None, replace=False) is None
    assert target_refusal("pilot", exists=True, marker=marker, replace=True) is None
    assert "pass --replace" in (
        target_refusal("pilot", exists=True, marker=marker, replace=False) or ""
    )
    for replace_flag in (False, True):
        refusal = target_refusal("pilot", exists=True, marker=None, replace=replace_flag)
        assert refusal is not None and "never dropped" in refusal


def test_size_is_estimated_from_the_sources_bytes_per_row() -> None:
    assert estimate_bytes(500, table_rows=1_000, table_bytes=10_000) == 5_000
    assert estimate_bytes(2_000, table_rows=1_000, table_bytes=10_000) == 10_000
    assert estimate_bytes(0, table_rows=1_000, table_bytes=10_000) == 0
    assert estimate_bytes(5, table_rows=0, table_bytes=10_000) == 0


def test_fingerprints_add_up_across_chunks() -> None:
    assert Fingerprint(2, 10, -3) + Fingerprint(1, 5, 7) == Fingerprint(3, 15, 4)
    assert Fingerprint() + Fingerprint(1, 1, 1) == Fingerprint(1, 1, 1)
    assert Fingerprint(1, 1, 1) != Fingerprint(1, 1, 2)


def test_bytes_are_shown_in_readable_units() -> None:
    assert format_bytes(0) == "0 B"
    assert format_bytes(2048) == "2.0 kB"
    assert format_bytes(5 * 1024**2) == "5.0 MB"
    assert format_bytes(int(4.8 * 1024**3)) == "4.80 GB"


def _plan(**overrides: object) -> PilotPlan:
    tables = (
        PlannedTable(_spec("core.match_resolution_rules"), True, 5_267, 5_267, 6_000_000),
        PlannedTable(_spec("core.translation_rule_definitions"), False, 0, 0, 0),
        PlannedTable(_spec("core.vehicles"), True, 500_000, 7_193_254, 450_000_000),
        PlannedTable(_spec("staging.transportstyrelsen_raw"), True, 499_516, 7_255_433,
                     790_000_000),
        PlannedTable(_spec("core.match_run_checkpoints"), True, 0, 0, 0),
        PlannedTable(_spec("core.remote_passenger_import_parts"), True, 0, 528, 0),
    )
    plan = PilotPlan(
        options=OPTIONS, source_database="app", target_exists=False, target_marker=None,
        eligible_vehicles=6_427_730, slice_vehicles=500_000, slice_records=499_516,
        catalog_status="completed", tables=tables, problems=(), warnings=(),
        sample_vehicles=30_000, sample_in_slice=30_000,
    )
    return replace(plan, **overrides)  # type: ignore[arg-type]


def test_the_plan_totals_rows_and_bytes_per_class() -> None:
    plan = _plan()

    assert plan.rows("A") == 5_267
    assert plan.rows("B") == 999_516
    assert plan.rows("C") == 0
    assert plan.estimated_bytes("B") == 1_240_000_000
    assert plan.estimated_bytes() == 1_246_000_000


def test_the_printed_plan_lists_every_table_with_rows_and_size() -> None:
    printed = format_plan(_plan())

    assert "source database   app (read-only)" in printed
    assert "target database   northstar_pilot (does not exist)" in printed
    assert "500,000 of 6,427,730 vehicles (scope passenger, registered only)" in printed
    assert "seed              northstar-match-impact-v1" in printed
    assert ("30k sample        30,000 of 30,000 match impact sample cars are in the slice "
            "(contained)") in printed
    assert "catalog batch     batch-1 (completed)" in printed
    lines = {line.split()[0]: line.split() for line in printed.splitlines() if line.strip()}
    assert lines["core.vehicles"][1:] == ["500,000", "7,193,254", "429.2", "MB"]
    assert lines["core.match_resolution_rules"][1:] == ["5,267", "5,267", "5.7", "MB"]
    assert lines["core.remote_passenger_import_parts"][1:] == ["528"]
    assert "core.translation_rule_definitions *" in printed
    assert "* not in the source" in printed
    assert "Estimated pilot size: 1.16 GB" in printed
    assert "Problems" not in printed and "Warnings" not in printed


def test_the_printed_plan_names_problems_warnings_and_an_existing_target() -> None:
    printed = format_plan(_plan(
        target_exists=True, target_marker={"state": "building"}, catalog_status=None,
        problems=("catalog batch batch-1 is not in the source",),
        warnings=("core.match_run_checkpoints holds 3 rows in the source",),
    ))

    assert "(exists: pilot build, state 'building')" in printed
    assert "batch-1 (NOT IN THE SOURCE)" in printed
    assert "Problems (the build refuses to start):\n  - catalog batch batch-1" in printed
    assert "Warnings:\n  - core.match_run_checkpoints holds 3 rows" in printed
    assert "EXISTS and is not a pilot build" in format_plan(_plan(target_exists=True))
    assert "2,298 of 30,000 match impact sample cars are in the slice (NOT contained)" in (
        format_plan(_plan(sample_in_slice=2_298))
    )


def test_a_dry_run_is_ok_unless_the_plan_has_problems() -> None:
    assert PilotOutcome(_plan(), built=False).ok
    assert not PilotOutcome(_plan(problems=("too few vehicles",)), built=False).ok


def test_a_build_is_ok_only_when_every_check_passed() -> None:
    passed = Check("slice size", True, "500,000 vehicles, asked for 500,000")
    failed = Check("core.vehicles (class B)", False, "499,999 rows, the source has 500,000")

    good = PilotOutcome(_plan(), built=True, checks=(passed,), database_bytes=5 * 1024**3)
    bad = PilotOutcome(_plan(), built=True, checks=(passed, failed))

    assert good.ok and not bad.ok
    summary = format_summary(good)
    assert "ok    slice size: 500,000 vehicles" in summary
    assert "1 of 1 checks passed" in summary and "5.00 GB on disk" in summary
    assert "FAILED" not in summary
    summary = format_summary(bad)
    assert "FAIL  core.vehicles (class B): 499,999 rows, the source has 500,000" in summary
    assert "1 of 2 checks passed; 1 FAILED -- do not ship this database" in summary


def test_a_database_error_is_named_without_its_message() -> None:
    error = psycopg.errors.UniqueViolation(
        'duplicate key value violates unique constraint "vehicles_pkey"\n'
        "DETAIL:  Key (vehicle_id)=(NOR-SECRET) already exists."
    )

    described = describe_database_error(error)

    assert described.startswith("UniqueViolation")
    assert "NOR-SECRET" not in described and "duplicate" not in described


MEASURED = {
    "core.vehicles": (("engine_code", "vehicle_id"), Fingerprint(500_000, 12, -34)),
    "core.match_resolution_rules": (("rule_id",), Fingerprint(5_267, 5, 6)),
    "core.match_run_checkpoints": ((), Fingerprint(0)),
}


def _manifest() -> dict[str, object]:
    return build_manifest(_plan(), MEASURED, source_snapshot_at="2026-10-02T08:00:00+00:00",
                          built_at="2026-10-02T09:00:00+00:00")


def test_the_manifest_records_the_cut_and_every_tables_count_and_checksum() -> None:
    manifest = _manifest()

    assert {key: value for key, value in manifest.items() if key != "tables"} == {
        MANIFEST_KEY: 1, "seed": "northstar-match-impact-v1", "size": 500_000,
        "scope": "passenger", "registered_only": True, "catalog_batch": "batch-1",
        "source_database": "app", "source_snapshot_at": "2026-10-02T08:00:00+00:00",
        "built_at": "2026-10-02T09:00:00+00:00", "slice_vehicles": 500_000,
        "ts_records": 499_516,
    }
    assert manifest["tables"] == {
        "core.match_resolution_rules": {"class": "A", "rows": 5_267, "checksum": "5:6",
                                        "columns": ["rule_id"]},
        "core.match_run_checkpoints": {"class": "C", "rows": 0, "checksum": "0:0",
                                       "columns": []},
        "core.vehicles": {"class": "B", "rows": 500_000, "checksum": "12:-34",
                          "columns": ["engine_code", "vehicle_id"]},
    }
    assert check_manifest(manifest) is manifest


@pytest.mark.parametrize(
    ("broken", "message"),
    [
        ({}, "not a pilot manifest"),
        ([], "not a pilot manifest"),
        ({MANIFEST_KEY: 2, "tables": {}}, "not a pilot manifest"),
        ({MANIFEST_KEY: 1, "tables": {}}, "lists no tables"),
        ({MANIFEST_KEY: 1, "tables": {"public.users": {}}}, "outside the pilot"),
        ({MANIFEST_KEY: 1, "tables": {"core.vehicles; DROP": {}}}, "outside the pilot"),
        ({MANIFEST_KEY: 1, "tables": {"core.vehicles": {"rows": 1}}}, "is incomplete"),
        ({MANIFEST_KEY: 1, "tables": {"core.vehicles": {
            "class": "B", "rows": "1", "checksum": "1:1", "columns": []}}}, "is incomplete"),
    ],
)
def test_a_file_that_is_not_a_manifest_is_refused(broken: object, message: str) -> None:
    with pytest.raises(PilotBuildError, match=message):
        check_manifest(broken)


def test_a_manifest_verification_names_the_tables_that_differ() -> None:
    passed = Check("core.match_resolution_rules (class A)", True, "5,267 rows")
    failed = Check("core.vehicles (class B)", False, "499,999 rows, the manifest has 500,000")

    good = format_manifest_verification("northstar_pilot", _manifest(), (passed,))
    bad = format_manifest_verification("northstar_pilot", _manifest(), (passed, failed))

    assert "seed northstar-match-impact-v1, size 500000, catalog batch batch-1" in good
    assert "source snapshot 2026-10-02T08:00:00+00:00" in good
    assert "1 of 1 checks passed" in good and "FAILED" not in good
    assert "FAIL  core.vehicles (class B): 499,999 rows, the manifest has 500,000" in bad
    assert bad.endswith("not the pilot that was built: core.vehicles (class B)")
