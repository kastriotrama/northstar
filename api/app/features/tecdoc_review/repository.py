from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol

from psycopg import Connection


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractContextManager[Connection[Any]]: ...


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
                    count(DISTINCT c.source_key) FILTER (WHERE c.entity_type='engine')
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
        return dict(zip(
            ("batch_id", "source_version", "source_rows", "promoted_ktypes", "manufacturers", "model_families", "engines"),
            row,
            strict=True,
        ))

    def fetch_vehicles(
        self, *, batch_id: str, query: str, limit: int, offset: int
    ) -> tuple[int, list[dict[str, Any]]]:
        search = f"%{query.strip()}%"
        statement = """
            WITH vehicles AS (
                SELECT a.source_key, a.node_id AS alias_id, a.attributes AS alias_attributes,
                    v.node_id AS variant_id, v.attributes AS variant_attributes,
                    v.source_row_refs,
                    m.attributes AS manufacturer_attributes,
                    f.attributes AS family_attributes,
                    e.attributes AS engine_attributes
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
                WHERE a.batch_id=%s AND a.entity_type='alias'
            )
        """
        condition = """WHERE %s='' OR concat_ws(' ', source_key,
            variant_attributes->>'source_name', manufacturer_attributes->>'canonical_name',
            family_attributes->>'canonical_name', engine_attributes->>'engine_code',
            engine_attributes->>'fuel_type') ILIKE %s"""
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(statement + "SELECT count(*) FROM vehicles " + condition, (batch_id, query.strip(), search))
            count_row = cursor.fetchone()
            total = int(count_row[0]) if count_row is not None else 0
            cursor.execute(
                statement + "SELECT * FROM vehicles " + condition + " ORDER BY source_key LIMIT %s OFFSET %s",
                (batch_id, query.strip(), search, limit, offset),
            )
            description = cursor.description
            if description is None:
                return total, []
            columns = [column.name for column in description]
            rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        return total, rows

    def fetch_entities(
        self, *, batch_id: str, kind: str, query: str, limit: int, offset: int
    ) -> tuple[int, list[dict[str, Any]]]:
        expressions = {
            "manufacturer": ("m.source_key", "m.attributes->>'canonical_name'", "m.attributes"),
            "model_family": ("f.source_key", "f.attributes->>'canonical_name'", "f.attributes"),
            "engine": ("e.source_key", "coalesce(e.attributes->>'engine_code', e.source_key)", "e.attributes"),
            "fuel": ("'fuel:' || (e.attributes->>'fuel_type')", "e.attributes->>'fuel_type'", "jsonb_build_object('fuel_type', e.attributes->>'fuel_type')"),
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
            cursor.execute(base + "SELECT count(*) FROM grouped " + condition, (batch_id, query.strip(), search))
            count_row = cursor.fetchone()
            total = int(count_row[0]) if count_row else 0
            cursor.execute(
                base + "SELECT source_key,name,details,vehicle_count,sample_ktypes FROM grouped "
                + condition + " ORDER BY vehicle_count DESC,name LIMIT %s OFFSET %s",
                (batch_id, query.strip(), search, limit, offset),
            )
            rows = [{"source_key": str(row[0]), "name": str(row[1]), "details": dict(row[2] or {}),
                     "vehicle_count": int(row[3]), "sample_ktypes": list(row[4] or [])}
                    for row in cursor.fetchall()]
        return total, rows
