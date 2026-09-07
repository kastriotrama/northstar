"""Official TecDoc key-table labels and conservative canonical mappings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

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
    # KT 182 describes the vehicle's alternate carrier. Retaining that carrier
    # separates otherwise identical petrol-engine KTypes during TS matching.
    "Petrol/Liquified Petroleum Gas (LPG)": "lpg",
    "Petrol/Ethanol": "ethanol",
    "Diesel/Electro": "hybrid_diesel",
}


_MIXED_ENGINE_FUEL_LABELS: dict[str, tuple[str, ...]] = {
    "Petrol/Alcohol": ("petrol", "alcohol_unspecified"),
    "Petrol/Ethanol": ("petrol", "ethanol"),
    "Petrol/Electric": ("petrol", "electric"),
    "Diesel/Electro": ("diesel", "electric"),
    "Flexfuel/Electric": ("flexfuel_unspecified", "electric"),
    "Petrol/Ethanol/Electric": ("petrol", "ethanol", "electric"),
    "Petrol/Electric/Liquefied Petroleum Gas (LPG)": ("petrol", "electric", "lpg"),
    "Petrol/Liquified Petroleum Gas (LPG)": ("petrol", "lpg"),
    "Petrol/Liquefied Petroleum Gas (LPG)": ("petrol", "lpg"),
}


@dataclass(frozen=True)
class EngineFuelEvidence:
    """KT088 descriptors, not proof of the fuel used by an individual vehicle.

    Mixed descriptors are not matcher equivalences, confirmed capabilities, or
    authority to select one fuel. In particular, Alcohol does not specify E85.
    """

    source_code: str | None
    official_label: str | None
    representation: Literal["single", "mixed", "unmapped", "missing"]
    components: tuple[str, ...]
    scalar_fuel_type: str | None
    version: str = "tecdoc-engine-fuel-evidence-v1"
    scope: str = "engine"
    key_table: str = "088"
    source_system: str = "tecdoc"

    def as_attributes(self) -> dict[str, object]:
        result = asdict(self)
        result["components"] = list(self.components)
        return result


def engine_fuel_evidence(
    code: str | None, labels: Mapping[str, str],
) -> EngineFuelEvidence:
    """Resolve exact official labels without guessing from a numeric code."""
    label = labels.get(code) if code else None
    if not label:
        return EngineFuelEvidence(code, label, "missing", (), None)
    if components := _MIXED_ENGINE_FUEL_LABELS.get(label):
        return EngineFuelEvidence(code, label, "mixed", components, None)
    if scalar := _ENGINE_FUEL_LABELS.get(label):
        return EngineFuelEvidence(code, label, "single", (scalar,), scalar)
    return EngineFuelEvidence(code, label, "unmapped", (), None)


def canonical_engine_fuels(
    reference_directory: Path, *, labels: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Map only officially labeled, unambiguous KT 088 fuels to graph values."""

    if labels is None:
        labels = load_key_table_labels(reference_directory, key_table_id="088")
    return {
        code: canonical
        for code in labels
        if (canonical := engine_fuel_evidence(code, labels).scalar_fuel_type) is not None
    }


def canonical_vehicle_fuels(reference_directory: Path) -> dict[str, str]:
    """Map unambiguous official KT 182 KType fuels to graph values."""

    labels = load_key_table_labels(reference_directory, key_table_id="182")
    return {
        code: canonical
        for code, label in labels.items()
        if (canonical := _ENGINE_FUEL_LABELS.get(label)) is not None
    }


def official_bodywork_labels(reference_directory: Path) -> dict[str, str]:
    """Return official English TecDoc KT 086 bodywork terminology."""

    return load_key_table_labels(reference_directory, key_table_id="086")


def official_transmission_type_labels(reference_directory: Path) -> dict[str, str]:
    """Return official English TecDoc KT 085 transmission terminology."""

    return load_key_table_labels(reference_directory, key_table_id="085")


_BODYWORK_CANONICAL_BY_KT086: dict[str, str] = {
    "021": "cargo_estate",
    "025": "hatchback",
    "027": "sedan",
    "028": "estate",
    "029": "coupe",
    "030": "convertible",
    "032": "pickup",
    "034": "van",
    "038": "suv",
    "039": "suv",
    "040": "multi_purpose_vehicle",
    "042": "chassis_cab",
    "048": "chassis_cab",
    "052": "van",
    "053": "suv",
    "054": "van",
    "055": "van",
}


def canonical_bodywork_by_kt086() -> dict[str, str]:
    """Return reviewed TecDoc body codes that safely map to NorthStar vocabulary."""

    return dict(_BODYWORK_CANONICAL_BY_KT086)


def official_drive_type_labels(reference_directory: Path) -> dict[str, str]:
    """Return official English TecDoc KT 082 drive terminology."""

    return load_key_table_labels(reference_directory, key_table_id="082")


def canonical_drive_by_kt082() -> dict[str, str]:
    """Map only wheel-drive classifications to NorthStar drive vocabulary."""

    return {
        "001": "fwd",
        "002": "rwd",
        "003": "awd",
        "004": "awd",
        "005": "awd",
        "011": "awd",
    }
