"""Official TecDoc key-table labels and conservative canonical mappings."""

from __future__ import annotations

from pathlib import Path

from ingestion.tecdoc.fixed_width import ParsedRow, read_table


def _required(row: ParsedRow, field: str) -> str:
    value = row.values[field]
    if value is None:
        raise ValueError(f"{row.source_ref} is missing required field {field}")
    return value


def language_id_for_iso(reference_directory: Path, iso_code: str) -> str:
    for row in read_table(reference_directory, "020"):
        if row.values["iso_code"] == iso_code:
            return _required(row, "language_id")
    raise ValueError(f"TecDoc language is missing for ISO code {iso_code!r}")


def load_key_table_labels(
    reference_directory: Path,
    *,
    key_table_id: str,
    iso_code: str = "en",
) -> dict[str, str]:
    language_id = language_id_for_iso(reference_directory, iso_code)
    descriptions: dict[str, str] = {}
    wanted: dict[str, str] = {}
    for row in read_table(reference_directory, "052"):
        if row.values["key_table_id"] == key_table_id and row.values["deleted"] != "1":
            wanted[_required(row, "description_id")] = _required(row, "key")
    for row in read_table(reference_directory, "030"):
        description_id = _required(row, "description_id")
        if (
            description_id in wanted
            and row.values["language_id"] == language_id
            and row.values["deleted"] != "1"
        ):
            descriptions[wanted[description_id]] = _required(row, "text")
    missing = set(wanted.values()) - descriptions.keys()
    if missing:
        raise ValueError(f"TecDoc key table {key_table_id} is missing labels: {sorted(missing)}")
    return descriptions


_ENGINE_FUEL_LABELS: dict[str, str] = {
    "Petrol": "petrol",
    "Super unleaded (95)": "petrol",
    "Superplus (98/99) Unleaded": "petrol",
    "Super (98)": "petrol",
    "Regular (91) unleaded": "petrol",
    "Ethanol Blended Petrol (E85)": "petrol",
    "Ethanol Blended Petrol (E10)": "petrol",
    "Ethanol Blended Petrol (E5)": "petrol",
    "Super (E10)": "petrol",
    "Diesel": "diesel",
    "Bio Diesel": "diesel",
    "Electric": "electric",
    "Hydrogen": "hydrogen",
    "Liquefied Petroleum Gas (LPG)": "lpg",
    "LPG": "lpg",
    "Natural Gas": "cng",
    "CNG": "cng",
    "Biogas": "cng",
    "Petrol/Electric": "hybrid_petrol",
    "Flexfuel/Electric": "hybrid_petrol",
    "Petrol/Ethanol/Electric": "hybrid_petrol",
    "Petrol/Electric/Liquefied Petroleum Gas (LPG)": "hybrid_petrol",
    "Diesel/Electro": "hybrid_diesel",
}


def canonical_engine_fuels(reference_directory: Path) -> dict[str, str]:
    """Map only officially labeled, unambiguous KT 088 fuels to graph values."""

    labels = load_key_table_labels(reference_directory, key_table_id="088")
    return {
        code: canonical
        for code, label in labels.items()
        if (canonical := _ENGINE_FUEL_LABELS.get(label)) is not None
    }
