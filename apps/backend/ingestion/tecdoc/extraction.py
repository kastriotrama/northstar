"""Validated TecDoc vehicle-tree extraction from a restored source schema."""

from __future__ import annotations

import re
from collections.abc import Iterator

from psycopg import Connection

from ingestion.tecdoc.models import TecDocVehicleRow

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")

# This view is the explicit boundary between the licensed TecDoc table layout
# and NorthStar. The restore runbook defines its columns and joins; application
# code never guesses fixed-width offsets from provider files.
VEHICLE_TREE_COLUMNS: tuple[str, ...] = tuple(TecDocVehicleRow.__dataclass_fields__)


def extract_vehicle_tree(
    connection: Connection,
    *,
    source_schema: str = "tecdoc_source",
    fetch_size: int = 2_000,
) -> Iterator[TecDocVehicleRow]:
    """Stream stable KType rows from ``<schema>.northstar_vehicle_tree``."""

    if not _IDENTIFIER.fullmatch(source_schema):
        raise ValueError("source_schema must be a lowercase PostgreSQL identifier")
    if fetch_size < 1:
        raise ValueError("fetch_size must be positive")
    columns = ", ".join(VEHICLE_TREE_COLUMNS)
    query = f"SELECT {columns} FROM {source_schema}.northstar_vehicle_tree ORDER BY ktype_id"
    with connection.cursor(name="tecdoc_vehicle_tree") as cursor:
        cursor.itersize = fetch_size
        cursor.execute(query)
        for values in cursor:
            yield TecDocVehicleRow.from_mapping(dict(zip(VEHICLE_TREE_COLUMNS, values, strict=True)))
