from ingestion.graph_migrations import (
    ALIAS_IDENTITY_CONSTRAINT,
    GRAPH_MIGRATION_STATEMENTS,
    LOOKUP_INDEXES,
    NODE_ID_CONSTRAINTS,
    NODE_LABELS_BY_PREFIX,
)
from northstar.node_ids import NodeIdPrefix


def test_every_canonical_label_has_an_id_uniqueness_constraint() -> None:
    assert set(NODE_LABELS_BY_PREFIX) == set(NodeIdPrefix)

    for label in NODE_LABELS_BY_PREFIX.values():
        matching = [
            statement
            for statement in NODE_ID_CONSTRAINTS
            if f"(n:{label})" in statement.cypher and "n.id IS UNIQUE" in statement.cypher
        ]
        assert len(matching) == 1, f"Missing or duplicate id constraint for {label}"


def test_every_statement_is_idempotent_and_named() -> None:
    for statement in GRAPH_MIGRATION_STATEMENTS:
        assert "IF NOT EXISTS" in statement.cypher
        assert statement.name in statement.cypher
        assert statement.name == statement.name.lower()


def test_statement_names_are_unique() -> None:
    names = [statement.name for statement in GRAPH_MIGRATION_STATEMENTS]
    assert len(names) == len(set(names))


def test_alias_identity_constraint_uses_computed_property() -> None:
    assert "n.assertion_identity IS UNIQUE" in ALIAS_IDENTITY_CONSTRAINT.cypher
    assert ALIAS_IDENTITY_CONSTRAINT.kind == "constraint"


def test_resolve_entry_index_matches_documented_property_order() -> None:
    resolve_entry = next(
        statement for statement in LOOKUP_INDEXES if statement.name == "alias_resolve_entry"
    )
    assert "(n.source_system, n.alias_type, n.alias_text)" in resolve_entry.cypher


def test_statement_kinds_match_cypher() -> None:
    for statement in GRAPH_MIGRATION_STATEMENTS:
        expected = "CREATE CONSTRAINT" if statement.kind == "constraint" else "CREATE INDEX"
        assert statement.cypher.startswith(expected)
        assert f"(n:{statement.label})" in statement.cypher
        assert statement.properties
        assert statement.schema_type == (
            "UNIQUENESS" if statement.kind == "constraint" else "RANGE"
        )
