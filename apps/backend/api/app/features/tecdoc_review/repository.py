from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Any, Protocol

from psycopg import Connection

from api.app.features.tecdoc_review.gaps import GAP_VALUE_SPECS, RESOLVABLE_FIELDS
from api.app.features.tecdoc_review.predicate import (
    FILTERABLE_FIELDS,
    GAP_FIELDS,
    compile_conditions,
)
from ingestion.tecdoc.resolution_migrations import (
    TECDOC_RESOLUTION_RULES_TABLE,
    run_tecdoc_resolution_migrations,
)

#: The `vehicles` CTE every filtered vehicle query builds on. Kept in one place
#: so `fetch_vehicles`, `count`, `facet` and `unresolved_summary` -- one column
#: list, one join, four uses -- can never drift into projecting different rows.
#:
#: The two trailing joins are the Tier 1 promotion-loop closer: a live ruling in
#: `core.tecdoc_resolution_rules` is coalesced in at read time rather than
#: written back into `core.tecdoc_canonical_candidates`, so resolving a value
#: takes effect on every screen immediately and reversibly -- change a ruling
#: later and the next query reflects it, with no batch data ever mutated. The
#: join is a plain equality on the raw code because `comparison_key` is a
#: no-op on a bare 3-digit KT082/KT086 code: no accents or punctuation to
#: strip. This does not reach the matcher (`tecdoc/match_run_adapters.py`),
#: which reads the same table through its own path -- see Tier 2.
_VEHICLES_CTE = """
    WITH vehicles AS (
        SELECT a.source_key, a.node_id AS alias_id, a.attributes AS alias_attributes,
            v.node_id AS variant_id, v.attributes AS variant_attributes,
            v.source_row_refs,
            m.attributes AS manufacturer_attributes,
            f.attributes AS family_attributes,
            e.attributes AS engine_attributes,
            t.attributes AS transmission_attributes,
            bw.attributes AS bodywork_attributes,
            bw_res.canonical_value AS bodywork_resolution_value,
            drv_res.canonical_value AS drive_resolution_value
        FROM core.tecdoc_canonical_candidates a
        JOIN core.tecdoc_canonical_candidates v
          ON v.batch_id=a.batch_id AND v.entity_type='vehicle_variant'
         AND v.source_key=a.attributes->>'target_source_key'
        LEFT JOIN core.tecdoc_canonical_candidates m
          ON m.batch_id=v.batch_id AND m.entity_type='manufacturer'
         AND m.source_key=v.attributes->>'manufacturer_source_key'
        LEFT JOIN core.tecdoc_canonical_candidates f
          ON f.batch_id=v.batch_id AND f.entity_type='model_family'
         AND f.source_key=v.attributes->>'model_family_source_key'
        LEFT JOIN core.tecdoc_canonical_candidates e
          ON e.batch_id=v.batch_id AND e.entity_type='engine'
         AND e.source_key=v.attributes->>'engine_source_key'
        LEFT JOIN core.tecdoc_canonical_candidates t
          ON t.batch_id=v.batch_id AND t.entity_type='transmission'
         AND t.source_key=v.attributes->>'transmission_source_key'
        LEFT JOIN core.tecdoc_canonical_candidates bw
          ON bw.batch_id=v.batch_id AND bw.entity_type='bodywork'
         AND bw.source_key=v.attributes->>'bodywork_source_key'
        LEFT JOIN core.tecdoc_resolution_rules bw_res
          ON bw_res.canonical_field='bodywork_form'
         AND bw_res.source_system='tecdoc'
         AND bw_res.comparison_key=v.attributes->>'tecdoc_body_type_code'
         AND bw_res.decision='accepted'
        LEFT JOIN core.tecdoc_resolution_rules drv_res
          ON drv_res.canonical_field='drive_type'
         AND drv_res.source_system='tecdoc'
         AND drv_res.comparison_key=v.attributes->>'tecdoc_drive_type_code'
         AND drv_res.decision='accepted'
        WHERE a.batch_id=%s AND a.entity_type='alias'
    )
"""

#: `query` free-text search reads the same concatenation `fetch_vehicles` always
#: has, so adding structured conditions never changes what plain search matches.
_SEARCH_CONDITION = """concat_ws(' ', source_key,
    variant_attributes->>'source_name', manufacturer_attributes->>'canonical_name',
    family_attributes->>'canonical_name', engine_attributes->>'engine_code',
    engine_attributes->>'fuel_type', transmission_attributes->>'transmission_code',
    bodywork_attributes->>'tecdoc_body_type_code') ILIKE %s"""


#: The value promotion already baked in for one `RESOLVABLE_FIELDS` entry, if
#: any -- read alongside `GAP_VALUE_SPECS`' raw term in `vehicle_detail` so a
#: field that promotion already resolved never reads as an open gap.
#: `transmission_type` has none: no promotion path computes a canonical value
#: for it today (see `gaps.py`), only a live rule ever can.
_PROMOTED_VALUE_EXPR: dict[str, str] = {
    "bodywork_form": "coalesce(bodywork_attributes->>'canonical_name', bodywork_resolution_value)",
    "drive_type": "coalesce(variant_attributes->>'drive_type', drive_resolution_value)",
    "energy_sources": "coalesce(engine_attributes->>'fuel_type', variant_attributes->>'fuel_type')",
    "transmission_type": "NULL",
}


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractContextManager[Connection[Any]]: ...


def _where(
    query: str,
    conditions: Sequence[Any],
    unresolved_field: str | None,
) -> tuple[str, list[Any]]:
    """One WHERE clause AND-ing free-text search, structured conditions and a gap."""

    fragments = ["(%s='' OR " + _SEARCH_CONDITION + ")"]
    parameters: list[Any] = [query.strip(), f"%{query.strip()}%"]
    compiled = compile_conditions(
        [(item.field, item.operator, tuple(item.values)) for item in conditions],
        unresolved_field=unresolved_field,
    )
    if compiled.sql != "true":
        fragments.append(compiled.sql)
        parameters.extend(compiled.parameters)
    return "WHERE " + " AND ".join(fragments), parameters


class TecDocReviewRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def latest_batch(self) -> dict[str, Any] | None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT b.batch_id, b.source_version, b.source_row_count,
                    count(*) FILTER (WHERE c.entity_type='alias'),
                    count(DISTINCT c.source_key) FILTER (WHERE c.entity_type='manufacturer'),
                    count(DISTINCT c.source_key) FILTER (WHERE c.entity_type='model_family'),
                    count(DISTINCT c.source_key) FILTER (WHERE c.entity_type='engine'),
                    count(*) FILTER (WHERE c.entity_type='vehicle_variant' AND c.attributes->>'engine_link_status'='linked'),
                    count(*) FILTER (WHERE c.entity_type='vehicle_variant' AND c.attributes->>'engine_link_status'='allocation_missing')
                FROM core.tecdoc_source_batches b
                JOIN core.tecdoc_canonical_candidates c ON c.batch_id=b.batch_id
                GROUP BY b.batch_id, b.source_version, b.source_row_count, b.created_at
                HAVING count(*) FILTER (WHERE c.entity_type='alias') > 0
                ORDER BY count(*) FILTER (WHERE c.entity_type='alias') DESC,
                         b.created_at DESC LIMIT 1
                """
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return dict(
            zip(
                (
                    "batch_id",
                    "source_version",
                    "source_rows",
                    "promoted_ktypes",
                    "manufacturers",
                    "model_families",
                    "engines",
                    "engine_linked_ktypes",
                    "facts_only_ktypes",
                ),
                row,
                strict=True,
            )
        )

    def fetch_vehicles(
        self,
        *,
        batch_id: str,
        query: str,
        limit: int,
        offset: int,
        conditions: Sequence[Any] = (),
        unresolved_field: str | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        where, where_params = _where(query, conditions, unresolved_field)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            run_tecdoc_resolution_migrations(connection)
            cursor.execute(
                _VEHICLES_CTE + "SELECT count(*) FROM vehicles " + where,
                [batch_id, *where_params],
            )
            count_row = cursor.fetchone()
            total = int(count_row[0]) if count_row is not None else 0
            cursor.execute(
                _VEHICLES_CTE
                + "SELECT * FROM vehicles "
                + where
                + " ORDER BY source_key LIMIT %s OFFSET %s",
                [batch_id, *where_params, limit, offset],
            )
            description = cursor.description
            if description is None:
                return total, []
            columns = [column.name for column in description]
            rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        return total, rows

    def count_vehicles(
        self,
        *,
        batch_id: str,
        query: str,
        conditions: Sequence[Any] = (),
        unresolved_field: str | None = None,
    ) -> int:
        where, where_params = _where(query, conditions, unresolved_field)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            run_tecdoc_resolution_migrations(connection)
            cursor.execute(
                _VEHICLES_CTE + "SELECT count(*) FROM vehicles " + where,
                [batch_id, *where_params],
            )
            row = cursor.fetchone()
        return int(row[0]) if row is not None else 0

    def total_vehicles(self, *, batch_id: str) -> int:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            run_tecdoc_resolution_migrations(connection)
            cursor.execute(
                _VEHICLES_CTE + "SELECT count(*) FROM vehicles",
                [batch_id],
            )
            row = cursor.fetchone()
        return int(row[0]) if row is not None else 0

    def facet_vehicles(
        self,
        *,
        batch_id: str,
        query: str,
        conditions: Sequence[Any],
        unresolved_field: str | None,
        field: str,
        limit: int,
    ) -> list[tuple[str, int]]:
        """Top values of one filterable field inside the current filter.

        The field's own clause, if any, is lifted before counting -- the same
        rule `vehicle_filter` follows -- so a field's siblings stay visible and
        their counts stay honest instead of a picked value hiding itself.
        """

        if field not in FILTERABLE_FIELDS:
            return []
        remaining = [item for item in conditions if item.field != field]
        where, where_params = _where(query, remaining, unresolved_field)
        expr = FILTERABLE_FIELDS[field]
        with self._connection_factory() as connection, connection.cursor() as cursor:
            run_tecdoc_resolution_migrations(connection)
            cursor.execute(
                _VEHICLES_CTE
                + f"SELECT {expr} AS value, count(*) AS rows FROM vehicles "
                + where
                + f" AND {expr} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT %s",
                [batch_id, *where_params, limit],
            )
            rows = cursor.fetchall()
        return [(str(value), int(count)) for value, count in rows]

    def unresolved_summary(
        self,
        *,
        batch_id: str,
        query: str,
        conditions: Sequence[Any],
    ) -> tuple[int, list[tuple[str, int]]]:
        """How many matched KTypes still miss each canonical gap, in one pass."""

        where, where_params = _where(query, conditions, None)
        gap_selects = ", ".join(
            f"count(*) FILTER (WHERE {predicate}) AS {name}"
            for name, (_, predicate) in GAP_FIELDS.items()
        )
        with self._connection_factory() as connection, connection.cursor() as cursor:
            run_tecdoc_resolution_migrations(connection)
            cursor.execute(
                _VEHICLES_CTE
                + f"SELECT count(*), {gap_selects} FROM vehicles "
                + where,
                [batch_id, *where_params],
            )
            row = cursor.fetchone()
        if row is None:
            return 0, []
        matched = int(row[0])
        counts = [(name, int(value)) for name, value in zip(GAP_FIELDS, row[1:], strict=True)]
        return matched, counts

    def fetch_entities(
        self, *, batch_id: str, kind: str, query: str, limit: int, offset: int
    ) -> tuple[int, list[dict[str, Any]]]:
        expressions = {
            "manufacturer": ("m.source_key", "m.attributes->>'canonical_name'", "m.attributes"),
            "model_family": ("f.source_key", "f.attributes->>'canonical_name'", "f.attributes"),
            "engine": (
                "e.source_key",
                "coalesce(e.attributes->>'engine_code', e.source_key)",
                "e.attributes",
            ),
            "fuel": (
                "'fuel:' || coalesce(e.attributes->>'fuel_type', v.attributes->>'fuel_type')",
                "coalesce(e.attributes->>'fuel_type', v.attributes->>'fuel_type')",
                "jsonb_build_object('fuel_type', coalesce(e.attributes->>'fuel_type', v.attributes->>'fuel_type'))",
            ),
            "bodywork": (
                "bw.source_key",
                "bw.attributes->>'canonical_name'",
                "bw.attributes",
            ),
            "transmission": (
                "t.source_key",
                "coalesce(t.attributes->>'transmission_code', t.source_key)",
                "t.attributes",
            ),
            "drive": (
                "'drive:' || (v.attributes->>'drive_type')",
                "upper(v.attributes->>'drive_type')",
                (
                    "jsonb_build_object('drive_type', v.attributes->>'drive_type', "
                    "'official_evidence', v.attributes->>'tecdoc_drive_official_label')"
                ),
            ),
        }
        source_expression, name_expression, details_expression = expressions[kind]
        base = f"""
            WITH vehicle_entities AS (
                SELECT a.attributes->>'alias_text' AS ktype,
                       {source_expression} AS source_key,
                       {name_expression} AS name,
                       {details_expression} AS details
                FROM core.tecdoc_canonical_candidates a
                JOIN core.tecdoc_canonical_candidates v
                  ON v.batch_id=a.batch_id AND v.entity_type='vehicle_variant'
                 AND v.source_key=a.attributes->>'target_source_key'
                LEFT JOIN core.tecdoc_canonical_candidates m
                  ON m.batch_id=v.batch_id AND m.entity_type='manufacturer'
                 AND m.source_key=v.attributes->>'manufacturer_source_key'
                LEFT JOIN core.tecdoc_canonical_candidates f
                  ON f.batch_id=v.batch_id AND f.entity_type='model_family'
                 AND f.source_key=v.attributes->>'model_family_source_key'
                LEFT JOIN core.tecdoc_canonical_candidates e
                  ON e.batch_id=v.batch_id AND e.entity_type='engine'
                 AND e.source_key=v.attributes->>'engine_source_key'
                LEFT JOIN core.tecdoc_canonical_candidates t
                  ON t.batch_id=v.batch_id AND t.entity_type='transmission'
                 AND t.source_key=v.attributes->>'transmission_source_key'
                LEFT JOIN core.tecdoc_canonical_candidates bw
                  ON bw.batch_id=v.batch_id AND bw.entity_type='bodywork'
                 AND bw.source_key=v.attributes->>'bodywork_source_key'
                WHERE a.batch_id=%s AND a.entity_type='alias'
            ), grouped AS (
                SELECT source_key, name, max(details::text)::jsonb AS details,
                       count(DISTINCT ktype) AS vehicle_count,
                       (array_agg(DISTINCT ktype ORDER BY ktype))[1:12] AS sample_ktypes
                FROM vehicle_entities
                WHERE source_key IS NOT NULL AND name IS NOT NULL
                GROUP BY source_key, name
            )
        """
        condition = "WHERE %s='' OR concat_ws(' ', source_key, name) ILIKE %s"
        search = f"%{query.strip()}%"
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                base + "SELECT count(*) FROM grouped " + condition,
                (batch_id, query.strip(), search),
            )
            count_row = cursor.fetchone()
            total = int(count_row[0]) if count_row else 0
            cursor.execute(
                base
                + "SELECT source_key,name,details,vehicle_count,sample_ktypes FROM grouped "
                + condition
                + " ORDER BY vehicle_count DESC,name LIMIT %s OFFSET %s",
                (batch_id, query.strip(), search, limit, offset),
            )
            rows = [
                {
                    "source_key": str(row[0]),
                    "name": str(row[1]),
                    "details": dict(row[2] or {}),
                    "vehicle_count": int(row[3]),
                    "sample_ktypes": list(row[4] or []),
                }
                for row in cursor.fetchall()
            ]
        return total, rows

    # --- the value-level gap: raw codes/labels with no canonical target -----------------

    def gap_values(
        self, *, batch_id: str, canonical_field: str, limit: int
    ) -> list[tuple[str, str | None, int]]:
        """Distinct raw values behind one canonical field's gap, largest first."""

        spec = GAP_VALUE_SPECS[canonical_field]
        label_sql = spec.label_expr or spec.value_expr
        with self._connection_factory() as connection, connection.cursor() as cursor:
            run_tecdoc_resolution_migrations(connection)
            cursor.execute(
                _VEHICLES_CTE
                + f"SELECT {spec.value_expr} AS source_term, max({label_sql}) AS label, "
                + "count(*) AS support FROM vehicles WHERE "
                + spec.unresolved_sql
                + " GROUP BY 1 ORDER BY 3 DESC, 1 LIMIT %s",
                [batch_id, limit],
            )
            rows = cursor.fetchall()
        return [
            (str(term), (str(label) if label is not None else None), int(support))
            for term, label, support in rows
        ]

    def vehicle_detail(self, *, batch_id: str, source_key: str) -> dict[str, Any] | None:
        """One promoted KType: every resolvable field's raw term and, if
        promotion already computed one, its baked-in canonical value.

        Whether a live rule *also* resolved it since is the service's job to
        merge in, exactly as `gap_values` splits the same concern at the
        population level -- this stays a plain read of the batch.
        """

        columns: list[str] = []
        for field in RESOLVABLE_FIELDS:
            spec = GAP_VALUE_SPECS[field]
            columns.append(spec.value_expr)
            columns.append(spec.label_expr or "NULL")
            columns.append(_PROMOTED_VALUE_EXPR[field])
        with self._connection_factory() as connection, connection.cursor() as cursor:
            run_tecdoc_resolution_migrations(connection)
            cursor.execute(
                _VEHICLES_CTE
                + "SELECT manufacturer_attributes->>'canonical_name', "
                + "family_attributes->>'canonical_name', "
                + ", ".join(columns)
                + " FROM vehicles WHERE source_key = %s",
                [batch_id, source_key],
            )
            row = cursor.fetchone()
        if row is None:
            return None
        manufacturer, model_family, *values = row
        fields: dict[str, dict[str, Any]] = {}
        for index, field in enumerate(RESOLVABLE_FIELDS):
            source_term, label, canonical_value = values[index * 3 : index * 3 + 3]
            fields[field] = {
                "source_term": None if source_term is None else str(source_term),
                "label": None if label is None else str(label),
                "canonical_value": None if canonical_value is None else str(canonical_value),
            }
        return {
            "manufacturer": manufacturer,
            "model_family": model_family,
            "fields": fields,
        }

    def fetch_resolutions(self, *, canonical_field: str) -> dict[str, dict[str, Any]]:
        """Every live-reviewed value for one canonical field, keyed by comparison_key.

        `to_regclass` first, exactly as `fetch_tecdoc_rules` does it: this table
        is created on first write, so a database no one has resolved anything
        in yet is the ordinary case rather than a fault.
        """

        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT to_regclass(%s) IS NOT NULL", (TECDOC_RESOLUTION_RULES_TABLE,)
            )
            exists_row = cursor.fetchone()
            if not (exists_row and exists_row[0]):
                return {}
            cursor.execute(
                "SELECT comparison_key, decision, canonical_value, note, reviewed_by, "
                f"updated_at FROM {TECDOC_RESOLUTION_RULES_TABLE} "
                "WHERE canonical_field = %s AND source_system = 'tecdoc'",
                (canonical_field,),
            )
            rows = cursor.fetchall()
        return {
            str(row[0]): {
                "decision": str(row[1]),
                "canonical_value": row[2],
                "note": str(row[3] or ""),
                "reviewed_by": str(row[4]),
                "updated_at": row[5].isoformat() if row[5] else "",
            }
            for row in rows
        }

    def upsert_resolution(
        self,
        *,
        canonical_field: str,
        comparison_key: str,
        source_term: str,
        key_table: str | None,
        decision: str,
        canonical_value: str | None,
        note: str,
        reviewed_by: str,
        source_system: str = "tecdoc",
        relation: str = "equivalent",
        support: int | None = None,
    ) -> dict[str, Any]:
        """Write one live ruling. Callers writing a `compatible` row (more than

        one canonical_value can apply to the same source term, e.g. TS's
        undifferentiated `2wd` against both TecDoc `fwd` and `rwd`) must use
        `insert_compatible_resolution` instead -- this upsert's conflict
        target is the single-target unique index and does not accept it.
        """

        if relation == "compatible":
            raise ValueError("upsert_resolution does not accept relation='compatible'")
        with self._connection_factory() as connection, connection.cursor() as cursor:
            run_tecdoc_resolution_migrations(connection)
            cursor.execute(
                f"""
                INSERT INTO {TECDOC_RESOLUTION_RULES_TABLE}
                    (canonical_field, source_system, comparison_key, source_term, key_table,
                     decision, canonical_value, relation, support, note, reviewed_by, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (canonical_field, source_system, comparison_key)
                    WHERE relation <> 'compatible' DO UPDATE SET
                    source_term = EXCLUDED.source_term,
                    key_table = EXCLUDED.key_table,
                    decision = EXCLUDED.decision,
                    canonical_value = EXCLUDED.canonical_value,
                    relation = EXCLUDED.relation,
                    support = EXCLUDED.support,
                    note = EXCLUDED.note,
                    reviewed_by = EXCLUDED.reviewed_by,
                    updated_at = now()
                RETURNING decision, canonical_value, note, reviewed_by, updated_at
                """,
                (
                    canonical_field,
                    source_system,
                    comparison_key,
                    source_term,
                    key_table,
                    decision,
                    canonical_value,
                    relation,
                    support,
                    note,
                    reviewed_by,
                ),
            )
            row = cursor.fetchone()
            connection.commit()
        if row is None:
            raise RuntimeError("resolution upsert returned no row")
        return {
            "decision": str(row[0]),
            "canonical_value": row[1],
            "note": str(row[2] or ""),
            "reviewed_by": str(row[3]),
            "updated_at": row[4].isoformat() if row[4] else "",
        }

    def insert_compatible_resolution(
        self,
        *,
        canonical_field: str,
        comparison_key: str,
        source_term: str,
        canonical_value: str,
        support: int,
        note: str,
        reviewed_by: str,
        source_system: str = "transportstyrelsen",
    ) -> dict[str, Any]:
        """Add one `compatible` pairing -- broader-than, never scored as a match.

        Unlike `upsert_resolution`, a source term can have more than one of
        these (TS's `2wd` is compatible with both TecDoc `fwd` and `rwd`), so
        conflict is keyed on the pair, not the source term alone.
        """

        with self._connection_factory() as connection, connection.cursor() as cursor:
            run_tecdoc_resolution_migrations(connection)
            cursor.execute(
                f"""
                INSERT INTO {TECDOC_RESOLUTION_RULES_TABLE}
                    (canonical_field, source_system, comparison_key, source_term, decision,
                     canonical_value, relation, support, note, reviewed_by, updated_at)
                VALUES (%s, %s, %s, %s, 'accepted', %s, 'compatible', %s, %s, %s, now())
                ON CONFLICT (canonical_field, source_system, comparison_key, canonical_value)
                    WHERE relation = 'compatible' DO UPDATE SET
                    source_term = EXCLUDED.source_term,
                    support = EXCLUDED.support,
                    note = EXCLUDED.note,
                    reviewed_by = EXCLUDED.reviewed_by,
                    updated_at = now()
                RETURNING decision, canonical_value, note, reviewed_by, updated_at
                """,
                (
                    canonical_field,
                    source_system,
                    comparison_key,
                    source_term,
                    canonical_value,
                    support,
                    note,
                    reviewed_by,
                ),
            )
            row = cursor.fetchone()
            connection.commit()
        if row is None:
            raise RuntimeError("compatible resolution insert returned no row")
        return {
            "decision": str(row[0]),
            "canonical_value": row[1],
            "note": str(row[2] or ""),
            "reviewed_by": str(row[3]),
            "updated_at": row[4].isoformat() if row[4] else "",
        }
