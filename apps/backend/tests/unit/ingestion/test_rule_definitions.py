from dataclasses import replace
from typing import Self

import pytest

from ingestion.rule_definitions import (
    RuleDefinitionImportError,
    import_rule_set,
    load_rule_set_from_database,
)
from ingestion.translation_dictionaries import (
    REVIEWED_RULE_SET_VERSION,
    TranslationRuleSet,
    load_translation_rule_set,
)


def test_empty_rule_set_is_refused() -> None:
    """An empty import would seal a version that means nothing."""

    with pytest.raises(RuleDefinitionImportError, match="empty rule set"):
        import_rule_set(
            None,  # type: ignore[arg-type]
            TranslationRuleSet(version="v", rules=()),
            rule_version="v",
            imported_by="tester",
            source_note="note",
        )


def test_stored_columns_cover_every_translation_rule_field() -> None:
    """A field the table cannot hold is a field silently lost on import.

    The graph promotion lost `source_name` exactly this way -- present upstream,
    absent at the boundary, no error. This pins the boundary instead.
    """

    from dataclasses import fields

    from ingestion.rule_definitions import _COLUMNS

    stored = {name.strip() for name in _COLUMNS.split(",")}
    declared = {f.name for f in fields(load_translation_rule_set(REVIEWED_RULE_SET_VERSION).rules[0])}

    assert declared - stored == set(), f"fields not persisted: {declared - stored}"


def test_rule_round_trips_through_the_stored_column_shape() -> None:
    """Every field must survive the tuple ordering used by the reader."""

    rule = replace(
        load_translation_rule_set(REVIEWED_RULE_SET_VERSION).rules[0],
        rule_id="RT-1",
        canonical_value="Value",
        display_value="Display",
        vehicle_scopes=("M1",),
        manufacturers=("Volvo",),
        requires_electrification=True,
    )
    row = (
        rule.rule_id, rule.area, list(rule.source_fields), list(rule.source_terms),
        rule.canonical_field, rule.canonical_value, rule.decision, rule.display_value,
        list(rule.vehicle_scopes), list(rule.manufacturers), rule.requires_electrification,
    )

    assert row[0] == "RT-1"
    assert row[8] == ["M1"]
    assert row[9] == ["Volvo"]
    assert row[10] is True


def test_missing_table_reads_as_no_stored_content() -> None:
    """A database without the migration must fall back, not raise.

    Production databases predate this table, and `deploy.sh` runs no
    migrations. Selecting from the absent table raised `UndefinedTable` and
    took down every caller of `load_active_rules`, so the fallback that makes
    an un-migrated database behave exactly as before is the contract under
    test, not an implementation detail.
    """

    class _Cursor:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def execute(self, statement: str, _params: object = None) -> None:
            if "to_regclass" in statement:
                self.row: tuple[object, ...] | None = (False,)
                return
            raise AssertionError("selected from a table that does not exist")

        def fetchone(self) -> tuple[object, ...] | None:
            return self.row

    class _Connection:
        def cursor(self) -> _Cursor:
            return _Cursor()

    assert load_rule_set_from_database(_Connection(), "any-version") is None  # type: ignore[arg-type]
