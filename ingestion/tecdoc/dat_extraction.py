"""Deterministic hierarchy extraction from TecDoc 2.7 `.dat` tables."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from ingestion.tecdoc.fixed_width import ParsedRow, read_table


@dataclass(frozen=True)
class EngineApplicability:
    sequence: str
    year_from: str | None
    year_to: str | None
    country_code: str | None
    exclude: bool
    source_row_ref: str


@dataclass(frozen=True)
class EngineAllocation:
    engine_id: str
    engine_code: str
    manufacturer_id: str
    fuel_type_code: str | None
    displacement_cc_from: int | None
    displacement_cc_to: int | None
    deleted: bool
    applicability: tuple[EngineApplicability, ...]
    engine_source_row_ref: str


@dataclass(frozen=True)
class TecDocHierarchyRecord:
    manufacturer_id: str
    manufacturer_name: str
    manufacturer_groups: tuple[str, ...]
    model_id: str
    model_name: str
    ktype_id: str
    ktype_name: str
    year_from: str | None
    year_to: str | None
    power_kw: int | None
    displacement_cc: int | None
    fuel_type_code: str | None
    drive_type_code: str | None
    transmission_type_code: str | None
    body_type_code: str | None
    engines: tuple[EngineAllocation, ...]
    source_row_refs: tuple[str, str, str]


def _required(row: ParsedRow, field: str) -> str:
    value = row.values[field]
    if value is None:
        raise ValueError(f"{row.source_ref} is missing required field {field}")
    return value


def _integer(value: str | None) -> int | None:
    return None if value is None else int(value)


def _load_descriptions(source_directory: Path, wanted: set[str], language_id: str) -> dict[str, str]:
    descriptions: dict[str, str] = {}
    for row in read_table(source_directory, "012"):
        description_id = _required(row, "description_id")
        if (
            description_id in wanted
            and row.values["language_id"] == language_id
            and row.values["country_code"] is None
        ):
            descriptions[description_id] = _required(row, "text")
    missing = wanted - descriptions.keys()
    if missing:
        sample = ", ".join(sorted(missing)[:10])
        raise ValueError(f"Missing language {language_id} descriptions: {sample}")
    return descriptions


def extract_dat_hierarchy(
    source_directory: Path,
    *,
    language_id: str = "004",
    limit: int | None = None,
) -> Iterator[TecDocHierarchyRecord]:
    """Yield active Table 120 KTypes with every valid Table 125 engine allocation."""

    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    manufacturer_rows = {
        _required(row, "manufacturer_id"): row
        for row in read_table(source_directory, "100")
        if row.values["deleted"] != "1"
    }
    model_rows = {
        _required(row, "model_id"): row
        for row in read_table(source_directory, "110")
        if row.values["deleted"] != "1" and row.values["is_pc"] == "1"
    }
    engine_rows = {
        _required(row, "engine_id"): row
        for row in read_table(source_directory, "155")
    }
    allocations: dict[str, list[ParsedRow]] = defaultdict(list)
    for row in read_table(source_directory, "125"):
        allocations[_required(row, "ktype_id")].append(row)

    ktypes: list[ParsedRow] = []
    for row in read_table(source_directory, "120"):
        if row.values["deleted"] == "1" or _required(row, "model_id") not in model_rows:
            continue
        ktypes.append(row)
        if limit is not None and len(ktypes) >= limit:
            break
    used_model_ids = {_required(row, "model_id") for row in ktypes}
    used_models = {model_id: model_rows[model_id] for model_id in used_model_ids}
    used_manufacturer_ids = {
        _required(row, "manufacturer_id") for row in used_models.values()
    }
    wanted_descriptions = {
        _required(manufacturer_rows[manufacturer_id], "description_id")
        for manufacturer_id in used_manufacturer_ids
    } | {_required(row, "description_id") for row in used_models.values()} | {
        _required(row, "description_id") for row in ktypes
    }
    descriptions = _load_descriptions(source_directory, wanted_descriptions, language_id)

    for ktype in ktypes:
        model = model_rows[_required(ktype, "model_id")]
        manufacturer = manufacturer_rows[_required(model, "manufacturer_id")]
        groups = tuple(
            name
            for name, field in (
                ("PC", "is_pc"), ("CV", "is_cv"), ("Axle", "is_axle"),
                ("Engine", "is_engine"), ("Transmission", "is_transmission"),
                ("LCV", "is_lcv"),
            )
            if manufacturer.values[field] == "1"
        )
        applicability_by_engine: dict[str, list[EngineApplicability]] = defaultdict(list)
        for allocation in allocations.get(_required(ktype, "ktype_id"), ()):
            engine_id = _required(allocation, "engine_id")
            applicability_by_engine[engine_id].append(
                EngineApplicability(
                    sequence=_required(allocation, "sequence"),
                    year_from=allocation.values["year_from"],
                    year_to=allocation.values["year_to"],
                    country_code=allocation.values["country_code"],
                    exclude=allocation.values["exclude"] == "1",
                    source_row_ref=allocation.source_ref,
                )
            )
        engine_allocations: list[EngineAllocation] = []
        for engine_id, applicability in applicability_by_engine.items():
            engine = engine_rows.get(engine_id)
            if engine is None:
                raise ValueError(
                    f"{applicability[0].source_row_ref} references missing engine {engine_id}"
                )
            engine_allocations.append(
                EngineAllocation(
                    engine_id=engine_id,
                    engine_code=_required(engine, "engine_code"),
                    manufacturer_id=_required(engine, "manufacturer_id"),
                    fuel_type_code=engine.values["fuel_type_code"],
                    displacement_cc_from=_integer(engine.values["displacement_cc_from"]),
                    displacement_cc_to=_integer(engine.values["displacement_cc_to"]),
                    deleted=engine.values["deleted"] == "1",
                    applicability=tuple(applicability),
                    engine_source_row_ref=engine.source_ref,
                )
            )
        yield TecDocHierarchyRecord(
            manufacturer_id=_required(manufacturer, "manufacturer_id"),
            manufacturer_name=descriptions[_required(manufacturer, "description_id")],
            manufacturer_groups=groups,
            model_id=_required(model, "model_id"),
            model_name=descriptions[_required(model, "description_id")],
            ktype_id=_required(ktype, "ktype_id"),
            ktype_name=descriptions[_required(ktype, "description_id")],
            year_from=ktype.values["year_from"],
            year_to=ktype.values["year_to"],
            power_kw=_integer(ktype.values["power_kw"]),
            displacement_cc=_integer(ktype.values["displacement_cc"]),
            fuel_type_code=ktype.values["fuel_type_code"],
            drive_type_code=ktype.values["drive_type_code"],
            transmission_type_code=ktype.values["transmission_type_code"],
            body_type_code=ktype.values["body_type_code"],
            engines=tuple(engine_allocations),
            source_row_refs=(manufacturer.source_ref, model.source_ref, ktype.source_ref),
        )
