import pytest

from ingestion.vehicle_facts_migrations import (
    CANONICAL_ONLY_FIELDS,
    NORMALIZED_INTEGER_FIELDS,
    NORMALIZED_TEXT_FIELDS,
    RESOLVABLE_FIELDS,
    SOURCE_INTEGER_COLUMNS,
    SOURCE_TEXT_COLUMNS,
    VEHICLE_FACTS_MIGRATIONS,
    canonical_only_columns,
    effective_value,
    unresolved_predicate,
)


def _statements() -> dict[str, str]:
    return dict(VEHICLE_FACTS_MIGRATIONS)


def test_every_projected_dimension_gets_a_column() -> None:
    table = _statements()["create_vehicle_facts_table"]

    assert "source_record_id BIGINT PRIMARY KEY" in table
    for column in SOURCE_TEXT_COLUMNS:
        assert f"{column} TEXT" in table
    for column in SOURCE_INTEGER_COLUMNS:
        assert f"{column} INTEGER" in table


def test_each_resolvable_field_has_both_a_normalized_and_a_rule_column() -> None:
    """Unresolved means neither half is filled, so both halves must exist."""

    table = _statements()["create_vehicle_facts_table"]

    for field in NORMALIZED_TEXT_FIELDS:
        assert f"n_{field} TEXT" in table
        assert f"r_{field} TEXT" in table
    for field in NORMALIZED_INTEGER_FIELDS:
        assert f"n_{field} INTEGER" in table
        assert f"r_{field} INTEGER" in table


def test_unresolved_predicate_requires_both_halves_absent() -> None:
    assert unresolved_predicate("drive_type") == (
        "n_drive_type IS NULL AND r_drive_type IS NULL"
    )
    assert unresolved_predicate("drive_type", alias="vf") == (
        "vf.n_drive_type IS NULL AND vf.r_drive_type IS NULL"
    )


def test_unresolved_predicate_rejects_unknown_fields() -> None:
    """Field names reach SQL by interpolation, so the whitelist is the guard."""

    with pytest.raises(ValueError):
        unresolved_predicate("drive_type; DROP TABLE core.vehicle_facts --")


def test_every_resolvable_field_gets_a_partial_index_over_its_gap() -> None:
    """The partial index *is* the unresolved set, so counting it never scans.

    Every field earns one, not just the large gaps: counting unresolved
    manufacturer -- 200 times rarer than drive_type -- took 9.16s without an
    index against drive_type's 1.07s with one, because without one the count is
    a heap scan whatever the answer turns out to be.
    """

    statements = _statements()

    for field in RESOLVABLE_FIELDS:
        index = statements[f"create_vehicle_facts_unresolved_{field}_index"]
        # Narrow on purpose: these serve counting, so the key only has to exist.
        assert "(source_record_id)" in index
        assert f"WHERE {unresolved_predicate(field)}" in index


def test_migrations_are_idempotent() -> None:
    for name, statement in VEHICLE_FACTS_MIGRATIONS:
        if statement.strip().startswith("CREATE TABLE"):
            assert "IF NOT EXISTS" in statement, name
        if statement.strip().startswith("CREATE INDEX"):
            assert "IF NOT EXISTS" in statement, name


def test_resolvable_fields_are_the_signature_fields() -> None:
    """A rule can only target something the matcher's signature is built from."""

    assert set(RESOLVABLE_FIELDS) == {
        "manufacturer",
        "model_family",
        "drive_type",
        "bodywork_form",
        "engine_code",
        "power_kw",
        "displacement_cc",
        "production_year",
    }


def test_a_reviewers_assertion_outranks_the_derivation_it_corrects() -> None:
    """Override rules fill `r_` on cars whose `n_` is already wrong, so every
    read has to take the rule first or the correction is invisible."""

    assert effective_value("bodywork_form") == (
        "coalesce(r_bodywork_form, n_bodywork_form)"
    )
    assert effective_value("power_kw", alias="vf", cast="text") == (
        "coalesce(vf.r_power_kw::text, vf.n_power_kw::text)"
    )


def test_effective_value_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError):
        effective_value("colour); DROP TABLE core.vehicle_facts --")


def test_the_effective_value_index_is_rebuilt_for_the_new_precedence() -> None:
    """An expression index cannot be altered in place, and recreating it under
    its old name would rebuild it on every startup."""

    statements = dict(VEHICLE_FACTS_MIGRATIONS)
    dropped = statements["drop_vehicle_facts_manufacturer_derived_first_index"]
    created = statements["create_vehicle_facts_manufacturer_effective_index"]

    assert dropped == "DROP INDEX IF EXISTS core.vehicle_facts_manufacturer_effective_idx"
    assert "vehicle_facts_manufacturer_asserted_idx" in created
    assert effective_value("manufacturer") in created


def test_canonical_only_columns_are_added_to_a_table_that_predates_them() -> None:
    """CREATE TABLE IF NOT EXISTS never alters a live table, which is how the first
    Vehicles release shipped code reading columns production did not have."""

    statements = _statements()
    for name in CANONICAL_ONLY_FIELDS:
        alter = statements[f"add_vehicle_facts_{name}_column"]
        assert f"ADD COLUMN IF NOT EXISTS {name} TEXT" in alter
        assert f"ON core.vehicle_facts ({name})" in statements[
            f"create_vehicle_facts_{name}_index"
        ]


def test_column_additions_are_idempotent_so_every_deploy_can_run_them() -> None:
    for name, statement in VEHICLE_FACTS_MIGRATIONS:
        if statement.strip().startswith("ALTER TABLE"):
            assert "IF NOT EXISTS" in statement, name


def test_vehicle_scope_is_a_plain_copy_of_what_normalization_stored() -> None:
    """Normalization owns the classification; the projection must not restate it."""

    assert dict(canonical_only_columns())["vehicle_scope"] == "norm.payload ->> 'vehicle_scope'"
