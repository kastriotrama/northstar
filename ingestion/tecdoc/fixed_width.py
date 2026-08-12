"""TecDoc Data Format 2.7 fixed-width readers for the vehicle hierarchy."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


class TecDocFormatError(ValueError):
    """Raised when a source row violates its documented fixed-width contract."""


@dataclass(frozen=True)
class Field:
    name: str
    position: int
    length: int


@dataclass(frozen=True)
class TableFormat:
    table_number: str
    row_length: int
    fields: tuple[Field, ...]
    reserved_prefix: bool = False


TABLE_FORMATS: dict[str, TableFormat] = {
    "012": TableFormat("012", 104, (
        Field("description_id", 29, 9), Field("country_code", 38, 3),
        Field("language_id", 41, 3), Field("text", 44, 60),
    ), reserved_prefix=True),
    "020": TableFormat("020", 47, (
        Field("language_id", 29, 3), Field("description_id", 32, 9),
        Field("iso_code", 41, 2), Field("codepage", 43, 4),
    ), reserved_prefix=True),
    "030": TableFormat("030", 102, (
        Field("description_id", 29, 9), Field("language_id", 38, 3),
        Field("text", 41, 60), Field("deleted", 101, 1),
    ), reserved_prefix=True),
    "052": TableFormat("052", 48, (
        Field("key_table_id", 29, 3), Field("key", 32, 3),
        Field("description_id", 35, 9), Field("sort_number", 44, 3),
        Field("deleted", 47, 1),
    ), reserved_prefix=True),
    "100": TableFormat("100", 40, (
        Field("manufacturer_id", 7, 6), Field("short_code", 13, 10),
        Field("description_id", 23, 9), Field("is_pc", 32, 1),
        Field("is_cv", 33, 1), Field("is_comparison", 34, 1),
        Field("is_axle", 35, 1), Field("is_engine", 36, 1),
        Field("is_transmission", 37, 1), Field("is_lcv", 38, 1),
        Field("deleted", 39, 1),
    )),
    "110": TableFormat("110", 47, (
        Field("model_id", 7, 5), Field("description_id", 12, 9),
        Field("manufacturer_id", 21, 6), Field("sort_number", 27, 3),
        Field("year_from", 30, 6), Field("year_to", 36, 6),
        Field("is_pc", 42, 1), Field("is_cv", 43, 1),
        Field("is_axle", 44, 1), Field("deleted", 45, 1), Field("is_lcv", 46, 1),
    )),
    "120": TableFormat("120", 107, (
        Field("ktype_id", 7, 9), Field("description_id", 16, 9),
        Field("model_id", 25, 5), Field("year_from", 32, 6),
        Field("year_to", 38, 6), Field("power_kw", 44, 4),
        Field("power_hp", 48, 4), Field("displacement_cc", 57, 5),
        Field("cylinders", 66, 2), Field("doors", 68, 1),
        Field("engine_type_code", 77, 3), Field("drive_type_code", 83, 3),
        Field("fuel_type_code", 94, 3), Field("transmission_type_code", 100, 3),
        Field("body_type_code", 103, 3), Field("deleted", 106, 1),
    )),
    "125": TableFormat("125", 62, (
        Field("ktype_id", 29, 9), Field("sequence", 38, 3),
        Field("engine_id", 41, 5), Field("year_from", 46, 6),
        Field("year_to", 52, 6), Field("country_code", 58, 3),
        Field("exclude", 61, 1),
    ), reserved_prefix=True),
    "155": TableFormat("155", 263, (
        Field("manufacturer_id", 7, 6), Field("engine_id", 13, 5),
        Field("engine_code", 18, 60), Field("year_from", 78, 6),
        Field("year_to", 84, 6), Field("power_kw_from", 90, 4),
        Field("power_kw_to", 94, 4), Field("displacement_cc_from", 138, 5),
        Field("displacement_cc_to", 143, 5), Field("fuel_type_code", 170, 3),
        Field("engine_type_code", 213, 3), Field("sales_term", 231, 30),
        Field("exclude", 261, 1), Field("deleted", 262, 1),
    )),
    "544": TableFormat("544", 90, (
        Field("transmission_id", 7, 5), Field("manufacturer_id", 12, 6),
        Field("transmission_code", 18, 30), Field("transmission_type_code", 48, 3),
        Field("year_from", 51, 6), Field("year_to", 57, 6),
        Field("transmission_identity", 78, 10), Field("speeds", 88, 2),
    )),
    "547": TableFormat("547", 61, (
        Field("ktype_id", 29, 9), Field("sequence", 38, 2),
        Field("transmission_id", 40, 5), Field("year_from", 45, 6),
        Field("year_to", 51, 6), Field("country_code", 57, 3),
        Field("exclude", 60, 1),
    ), reserved_prefix=True),
}


@dataclass(frozen=True)
class ParsedRow:
    table_number: str
    row_number: int
    values: dict[str, str | None]

    @property
    def source_ref(self) -> str:
        return f"{self.table_number}:{self.row_number}"


def parse_row(raw: str, *, row_number: int, table_format: TableFormat) -> ParsedRow:
    line = raw.rstrip("\r\n")
    if len(line) != table_format.row_length:
        raise TecDocFormatError(
            f"Table {table_format.table_number} row {row_number} has length {len(line)}; "
            f"expected {table_format.row_length}"
        )
    table_position = 26 if table_format.reserved_prefix else 4
    actual_table = line[table_position : table_position + 3]
    if actual_table != table_format.table_number:
        raise TecDocFormatError(
            f"Table {table_format.table_number} row {row_number} contains table marker "
            f"{actual_table!r}"
        )
    values = {
        field.name: (value if (value := line[field.position : field.position + field.length].strip()) else None)
        for field in table_format.fields
    }
    return ParsedRow(table_format.table_number, row_number, values)


def read_table(source_directory: Path, table_number: str) -> Iterator[ParsedRow]:
    try:
        table_format = TABLE_FORMATS[table_number]
    except KeyError as error:
        raise ValueError(f"Unsupported TecDoc table: {table_number}") from error
    path = source_directory / f"{table_number}.dat"
    if not path.is_file():
        raise FileNotFoundError(f"Required TecDoc table is missing: {path}")
    with path.open(encoding="utf-8-sig", errors="strict", newline="") as source:
        for row_number, raw in enumerate(source, start=1):
            yield parse_row(raw, row_number=row_number, table_format=table_format)
