"""What a NorthStar vehicle carries, and which source wins each value.

`core.vehicles` holds one row per physical vehicle. Every provider -- the
Transportstyrelsen (TS) register snapshot, the AIS VIN export, later ones --
reports *observations* about that vehicle; the row carries the value that
wins, and says where it came from. This module is the one statement of both:
the columns (the migration builds the table from it) and the precedence (every
writer merges through `vehicle_core_merge`, which reads it).

Precedence, highest first:

1. `review`  -- a reviewer's resolution rule. An explicit human assertion.
2. a provider (`transportstyrelsen`, `ais`), ordered by the field's policy:
   - `newest`:    registry facts that change over a vehicle's life (plate,
                  status, vehicle type, colour, fuel after a conversion). The
                  most recent observation wins.
   - `ts_first`:  technical detail only TS describes well (type approval,
                  variant, displacement, ...). TS wins; another provider only
                  fills a gap.
   - `any_first`: values TS never had (engine code, weights). Whichever
                  provider states it; the newest when two do.
3. `derived` -- computed from the vehicle's own data (an EV's power from its
   own EV power field).
4. `rule`    -- a learned enrichment rule (`vehicle_enrichment_rules`). Fills
   gaps only, never overrides anything a source stated.

A value that loses is not thrown away: it is kept in `field_alternatives`, so
retiring a rule or a review restores what the next source said.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

Policy = Literal["newest", "ts_first", "any_first", "derived", "matching"]
SqlType = Literal["text", "integer", "smallint", "date", "boolean", "text[]", "real"]

SOURCE_TS = "transportstyrelsen"
SOURCE_AIS = "ais"
SOURCE_REVIEW = "review"
SOURCE_RULE = "rule"
SOURCE_DERIVED = "derived"

PROVIDER_SOURCES: tuple[str, ...] = (SOURCE_TS, SOURCE_AIS)
ORIGIN_SOURCES: tuple[str, ...] = PROVIDER_SOURCES

REGISTRY_STATUSES: tuple[str, ...] = ("registered", "deregistered")


@dataclass(frozen=True)
class CoreField:
    """One value column of `core.vehicles`."""

    name: str
    sql_type: SqlType
    policy: Policy
    group: str
    label: str
    # A reviewer's resolution rule may target it (the TS data screen's rules).
    reviewable: bool = False
    # The Vehicles tab may filter on it and facet it.
    filterable: bool = False


CORE_FIELDS: tuple[CoreField, ...] = (
    # --- identity and lifecycle (vin/plate are maintained by the identifier logic)
    CoreField("vin", "text", "newest", "identity", "VIN", filterable=True),
    CoreField("plate", "text", "newest", "identity", "Plate", filterable=True),
    CoreField("registry_status", "text", "newest", "identity", "Registry status", filterable=True),
    CoreField("registry_status_observed_on", "date", "newest", "identity", "Status observed on"),
    CoreField("registry_vehicle_type", "text", "newest", "identity", "Registry vehicle type", filterable=True),
    CoreField("eu_category", "text", "newest", "identity", "EU category", filterable=True),
    CoreField("vehicle_scope", "text", "newest", "identity", "Vehicle type", filterable=True),
    # --- make and model
    CoreField("manufacturer", "text", "ts_first", "make", "Manufacturer", reviewable=True, filterable=True),
    CoreField("model_family", "text", "ts_first", "make", "Model family", reviewable=True, filterable=True),
    CoreField("registry_make_code", "text", "ts_first", "make", "Registry make code", filterable=True),
    CoreField("registry_brand_text", "text", "ts_first", "make", "Registry brand text", filterable=True),
    CoreField("registry_model_text", "text", "ts_first", "make", "Registry model text", filterable=True),
    CoreField("registry_type_code", "text", "ts_first", "make", "Registry type code", filterable=True),
    CoreField("variant_code", "text", "ts_first", "make", "Variant", filterable=True),
    CoreField("version_code", "text", "ts_first", "make", "Version", filterable=True),
    CoreField("type_approval", "text", "ts_first", "make", "Type approval", filterable=True),
    CoreField("group_code", "text", "ts_first", "make", "Group code", filterable=True),
    # --- technical
    CoreField("engine_code", "text", "any_first", "technical", "Engine code", reviewable=True, filterable=True),
    CoreField("power_kw", "integer", "newest", "technical", "Power (kW)", reviewable=True, filterable=True),
    CoreField("ev_power_kw", "integer", "ts_first", "technical", "EV power (kW)", filterable=True),
    CoreField("displacement_cc", "integer", "ts_first", "technical", "Displacement (cc)", reviewable=True, filterable=True),
    CoreField("fuel", "text", "newest", "technical", "Fuel", filterable=True),
    CoreField("fuel_secondary", "text", "newest", "technical", "Second fuel", filterable=True),
    CoreField("fuel_match_tokens", "text[]", "newest", "technical", "Fuel match tokens"),
    CoreField("electrification_type", "text", "newest", "technical", "Electrification", filterable=True),
    CoreField("transmission", "text", "newest", "technical", "Transmission", filterable=True),
    CoreField("drive_type", "text", "ts_first", "technical", "Drive type", reviewable=True, filterable=True),
    CoreField("registry_all_wheel_drive", "boolean", "ts_first", "technical", "Registry 4WD flag"),
    CoreField("bodywork_form", "text", "newest", "technical", "Bodywork", reviewable=True, filterable=True),
    CoreField("registry_body_code", "text", "newest", "technical", "Registry body code", filterable=True),
    CoreField("emission_standard", "text", "ts_first", "technical", "Euro class", filterable=True),
    # --- dates
    CoreField("production_year", "smallint", "ts_first", "dates", "Production year", reviewable=True, filterable=True),
    CoreField("production_month", "smallint", "ts_first", "dates", "Production month", filterable=True),
    CoreField("model_year", "smallint", "ts_first", "dates", "Model year", filterable=True),
    CoreField("registry_vehicle_year", "smallint", "ts_first", "dates", "Registry vehicle year", filterable=True),
    CoreField("first_registration_date", "date", "newest", "dates", "First registered", filterable=True),
    # --- physical
    CoreField("colour", "text", "newest", "physical", "Colour", filterable=True),
    CoreField("kerb_weight_kg", "integer", "any_first", "physical", "Kerb weight (kg)", filterable=True),
    CoreField("max_weight_kg", "integer", "any_first", "physical", "Max weight (kg)", filterable=True),
    CoreField("length_mm", "integer", "any_first", "physical", "Length (mm)", filterable=True),
    CoreField("wheelbase_mm", "integer", "ts_first", "physical", "Wheelbase (mm)", filterable=True),
    CoreField("tyre_front", "text", "ts_first", "physical", "Front tyre", filterable=True),
    CoreField("tyre_rear", "text", "ts_first", "physical", "Rear tyre", filterable=True),
    CoreField("seats", "smallint", "ts_first", "physical", "Seats", filterable=True),
    # --- the accepted TecDoc match; written by matching, never by a provider
    CoreField("vehicle_variant_id", "text", "matching", "match", "Vehicle variant"),
    CoreField("ktype", "text", "matching", "match", "KType", filterable=True),
    CoreField("match_state", "text", "matching", "match", "Match state", filterable=True),
    # --- how the origin record normalized
    CoreField("normalization_status", "text", "ts_first", "normalization", "Normalization status", filterable=True),
    CoreField("normalization_confidence", "real", "ts_first", "normalization", "Normalization confidence"),
)

FIELDS_BY_NAME: dict[str, CoreField] = {field.name: field for field in CORE_FIELDS}
FIELD_NAMES: tuple[str, ...] = tuple(field.name for field in CORE_FIELDS)
REVIEWABLE_FIELDS: tuple[str, ...] = tuple(field.name for field in CORE_FIELDS if field.reviewable)
FILTERABLE_FIELDS: tuple[str, ...] = tuple(field.name for field in CORE_FIELDS if field.filterable)
# Fields a provider observation may set. Match columns belong to matching, not to
# any provider, so they are never merged from a source.
MERGED_FIELDS: tuple[str, ...] = tuple(
    field.name for field in CORE_FIELDS if field.policy not in ("matching", "derived")
)
INTEGER_TYPES = frozenset({"integer", "smallint"})


# --- source references -----------------------------------------------------------------
#
# A value's source is written `<source>[:<ref>][@<YYYY-MM-DD>]`, e.g.
# `ais:2026-09-19T10:28:27@2026-09-19`, `transportstyrelsen:1001962868@2026-08-07`,
# `review:4f7c...`, `rule:ENG-VV-1a2b3c`. A field missing from `field_sources` came
# from the vehicle's origin record, observed on `origin_observed_on`.

_REF_PATTERN = re.compile(r"^(?P<source>[a-z_]+)(?::(?P<ref>[^@]*))?(?:@(?P<date>\d{4}-\d{2}-\d{2}))?$")


@dataclass(frozen=True)
class SourceRef:
    source: str
    ref: str | None = None
    observed_on: date | None = None

    def encode(self) -> str:
        text = self.source
        if self.ref:
            text += f":{self.ref}"
        if self.observed_on is not None:
            text += f"@{self.observed_on.isoformat()}"
        return text


def parse_source_ref(text: str) -> SourceRef:
    match = _REF_PATTERN.match(text or "")
    if match is None:
        raise ValueError(f"not a source reference: {text!r}")
    observed = match.group("date")
    return SourceRef(
        source=match.group("source"),
        ref=match.group("ref") or None,
        observed_on=date.fromisoformat(observed) if observed else None,
    )


# --- value cleaning shared by every writer ---------------------------------------------

_UNKNOWN_TEXT = frozenset({"", "OKÄND", "OKAND", "UNKNOWN", "-"})
_VIN_PATTERN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


def clean_text(value: object) -> str | None:
    """Registry text with surrounding space removed; empty and 'unknown' are absence."""

    if value is None:
        return None
    text = " ".join(str(value).split())
    if text.upper() in _UNKNOWN_TEXT:
        return None
    return text


def clean_code(value: object) -> str | None:
    """An identifier-like code: trimmed and upper-cased."""

    text = clean_text(value)
    return text.upper() if text else None


def clean_int(value: object, *, zero_is_absent: bool = True) -> int | None:
    """Digits only; `0` is how the registry writes 'not recorded' for a measure."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            number = int(float(text)) if "." in text else int(text)
        except ValueError:
            return None
    if zero_is_absent and number == 0:
        return None
    return number


def clean_registry_date(value: object) -> date | None:
    """`YYYYMMDD` or ISO `YYYY-MM-DD`; anything else is absent."""

    text = clean_text(value)
    if not text:
        return None
    digits = text.replace("-", "")
    if len(digits) != 8 or not digits.isdigit():
        return None
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


def clean_year_month(value: object) -> tuple[int | None, int | None]:
    """`YYYYMM` (or a longer date) into (year, month)."""

    text = clean_text(value)
    if not text:
        return None, None
    digits = text.replace("-", "")
    if len(digits) < 6 or not digits[:6].isdigit():
        return None, None
    year, month = int(digits[:4]), int(digits[4:6])
    if not 1 <= month <= 12 or year < 1880:
        return None, None
    return year, month


def is_strong_vin(value: object) -> bool:
    """A full 17-character VIN. Shorter chassis numbers repeat across old cars."""

    code = clean_code(value)
    return bool(code and _VIN_PATTERN.match(code))


def clean_engine_code(value: object) -> str | None:
    """An engine code, or None for the junk the AIS export carries (`1`, `D`, ...)."""

    code = clean_code(value)
    if code is None:
        return None
    compact = re.sub(r"[^0-9A-Z]", "", code)
    if len(compact) < 3 or compact.isdigit():
        return None
    return code


def normalize_plate(value: object) -> str | None:
    code = clean_code(value)
    return code.replace(" ", "") if code else None
