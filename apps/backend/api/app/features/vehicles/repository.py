"""Reads of `core.vehicles`, its identifiers, source links and learned rules."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from psycopg import Connection

from ingestion.vehicle_core_fields import FIELD_NAMES
from ingestion.vehicle_core_migrations import (
    VEHICLE_ENRICHMENT_RULES_TABLE,
    VEHICLE_IDENTIFIERS_TABLE,
    VEHICLE_SOURCE_LINKS_TABLE,
    VEHICLES_TABLE,
)
from ingestion.vehicle_core_query import (
    ALIAS,
    VehicleTerm,
    compile_vehicle_filter,
    filterable_column,
    resolve_search,
)
from ingestion.vehicle_match_result_migrations import VEHICLE_MATCH_RESULTS_TABLE


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractContextManager[Connection[Any]]: ...


#: The list's columns, in `VehicleRow` order after `vehicle_id`.
LIST_COLUMNS: tuple[str, ...] = (
    "plate",
    "vin",
    "registry_status",
    "vehicle_scope",
    "manufacturer",
    "model_family",
    "production_year",
    "power_kw",
    "displacement_cc",
    "engine_code",
    "fuel",
    "transmission",
    "drive_type",
    "bodywork_form",
    "colour",
    "ktype",
    "match_state",
)

_META_COLUMNS: tuple[str, ...] = (
    "vehicle_id",
    "origin_source",
    "origin_observed_on",
    "ts_record_id",
    "field_sources",
    "field_alternatives",
    "created_at",
    "updated_at",
)

# Which fields a person or a learned rule supplied, read from `field_sources`
# in the query so the list never ships every row's source map to Python.
_ASSERTED_FIELDS = (
    "ARRAY(SELECT source.key FROM jsonb_each_text({alias}.field_sources) AS source "
    "WHERE source.value LIKE ANY(%s) ORDER BY source.key)"
)
# A reviewer's rule and a person's KType choice are `review:`, a person's
# correction of one car `correction:`: all of them are a person's word.
_PERSON_SOURCES = ["review%", "correction%"]
_RULE_SOURCES = ["rule%"]
#: The car's stored match result, read by the vehicle's key: its state and the
#: KType the matcher accepted. Two index probes a row; no join, so the list's
#: order and paging stay those of `core.vehicles`.
_STORED_RESULT = (
    "(SELECT stored_result.{column} FROM " + VEHICLE_MATCH_RESULTS_TABLE + " AS stored_result "
    "WHERE stored_result.vehicle_id = {alias}.vehicle_id)"
)
LINK_LIMIT = 100


@dataclass(frozen=True)
class VehicleRecordRows:
    vehicle: dict[str, Any]
    identifiers: list[dict[str, Any]]
    links: list[dict[str, Any]]
    link_count: int


class VehicleRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def page(
        self,
        terms: Sequence[VehicleTerm],
        text: str,
        *,
        after: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """One keyset page, ordered by `vehicle_id` (a ULID: oldest vehicle first)."""

        columns = ", ".join(f"{ALIAS}.{column}" for column in LIST_COLUMNS)
        asserted = _ASSERTED_FIELDS.format(alias=ALIAS)
        cursor_sql = f" AND {ALIAS}.vehicle_id > %s" if after else ""
        with self._connection_factory() as connection:
            predicate = compile_vehicle_filter(terms, resolve_search(connection, text))
            parameters: list[Any] = [_PERSON_SOURCES, _RULE_SOURCES, *predicate.parameters]
            if after:
                parameters.append(after)
            parameters.append(limit)
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT {ALIAS}.vehicle_id, {columns}, {asserted}, {asserted}, "
                    f"{_STORED_RESULT.format(column='state', alias=ALIAS)}, "
                    f"{_STORED_RESULT.format(column='ktype', alias=ALIAS)}, "
                    f"{_STORED_RESULT.format(column='candidate_ktypes', alias=ALIAS)}, "
                    f"{_STORED_RESULT.format(column='candidate_confidences', alias=ALIAS)} "
                    f"FROM {VEHICLES_TABLE} AS {ALIAS} "
                    f"WHERE {predicate.sql}{cursor_sql} "
                    f"ORDER BY {ALIAS}.vehicle_id LIMIT %s",
                    parameters,
                )
                rows = cursor.fetchall()
        names = ("vehicle_id", *LIST_COLUMNS, "review_fields", "rule_fields",
                 "match_result", "automatic_ktype", "candidate_ktypes",
                 "candidate_confidences")
        found = [dict(zip(names, row, strict=True)) for row in rows]
        for item in found:
            # A car without a stored result has no row to read the arrays from.
            item["candidate_ktypes"] = list(item["candidate_ktypes"] or [])
            item["candidate_confidences"] = list(item["candidate_confidences"] or [])
        return found

    def count(self, terms: Sequence[VehicleTerm], text: str) -> int:
        with self._connection_factory() as connection:
            predicate = compile_vehicle_filter(terms, resolve_search(connection, text))
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT count(*) FROM {VEHICLES_TABLE} AS {ALIAS} WHERE {predicate.sql}",
                    predicate.parameters,
                )
                row = cursor.fetchone()
        return int(row[0]) if row else 0

    def facet(
        self, terms: Sequence[VehicleTerm], text: str, *, field: str, limit: int
    ) -> list[tuple[str, int]]:
        """Top values of one field inside the filter, its own clauses lifted."""

        column = filterable_column(field)
        with self._connection_factory() as connection:
            predicate = compile_vehicle_filter(
                terms, resolve_search(connection, text), skip_field=field
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT {column}::text, count(*) FROM {VEHICLES_TABLE} AS {ALIAS} "
                    f"WHERE {predicate.sql} AND {column} IS NOT NULL "
                    f"GROUP BY {column} ORDER BY count(*) DESC, {column}::text LIMIT %s",
                    [*predicate.parameters, limit],
                )
                rows = cursor.fetchall()
        return [(str(value), int(count)) for value, count in rows]

    def record(self, vehicle_id: str) -> VehicleRecordRows | None:
        columns = ", ".join((*_META_COLUMNS, *FIELD_NAMES))
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {columns} FROM {VEHICLES_TABLE} WHERE vehicle_id = %s", (vehicle_id,)
            )
            row = cursor.fetchone()
            if row is None:
                return None
            vehicle = dict(zip((*_META_COLUMNS, *FIELD_NAMES), row, strict=True))
            cursor.execute(
                "SELECT kind, value, valid_from, valid_to, source, source_ref "
                f"FROM {VEHICLE_IDENTIFIERS_TABLE} WHERE vehicle_id = %s "
                "ORDER BY valid_to IS NOT NULL, valid_to DESC, kind, valid_from DESC NULLS LAST, "
                "value",
                (vehicle_id,),
            )
            identifiers = [
                dict(zip(("kind", "value", "valid_from", "valid_to", "source", "source_ref"), item,
                         strict=True))
                for item in cursor.fetchall()
            ]
            cursor.execute(
                "SELECT source_system, source_record_key, observed_on, link_method, linked_at "
                f"FROM {VEHICLE_SOURCE_LINKS_TABLE} WHERE vehicle_id = %s "
                "ORDER BY observed_on DESC NULLS LAST, source_system, source_record_key LIMIT %s",
                (vehicle_id, LINK_LIMIT),
            )
            links = [
                dict(zip(("source_system", "source_record_key", "observed_on", "link_method",
                          "linked_at"), item, strict=True))
                for item in cursor.fetchall()
            ]
            if len(links) < LINK_LIMIT:
                link_count = len(links)
            else:
                cursor.execute(
                    f"SELECT count(*) FROM {VEHICLE_SOURCE_LINKS_TABLE} WHERE vehicle_id = %s",
                    (vehicle_id,),
                )
                counted = cursor.fetchone()
                link_count = int(counted[0]) if counted else len(links)
        return VehicleRecordRows(vehicle, identifiers, links, link_count)

    def rules(self, rule_ids: Sequence[str]) -> list[dict[str, Any]]:
        if not rule_ids:
            return []
        names = ("rule_id", "rule_family", "target_field", "key_fields", "key_values", "value",
                 "support", "agreement", "status")
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {', '.join(names)} FROM {VEHICLE_ENRICHMENT_RULES_TABLE} "
                "WHERE rule_id = ANY(%s) ORDER BY rule_id",
                (sorted(set(rule_ids)),),
            )
            return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
