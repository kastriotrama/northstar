"""Prepare safe canonical graph promotions from real TecDoc hierarchy records."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from psycopg import Connection

from ingestion.tecdoc.dat_extraction import (
    EngineAllocation,
    TecDocHierarchyRecord,
    TransmissionAllocation,
)
from ingestion.tecdoc.models import CanonicalCandidate
from ingestion.tecdoc.reference_data import engine_fuel_evidence
from ingestion.tecdoc.repository import get_or_mint_node_id, write_candidate
from northstar.alias_identity import build_assertion_identity


@dataclass(frozen=True)
class CanonicalPromotion:
    manufacturer_id: str
    manufacturer_name: str
    model_family_id: str
    model_family_name: str
    variant_id: str
    year_from: int
    year_to: int | None
    engine_id: str | None
    engine_code: str | None
    displacement_cc: int | None
    displacement_source: str | None
    fuel_type: str | None
    tecdoc_fuel_code: str | None
    tecdoc_engine_type_code: str | None
    engine_link_status: str
    power_kw: int | None
    alias_id: str
    alias_text: str
    source_record_key: str
    source_assertion_key: str
    assertion_identity: str
    bodywork_id: str | None = None
    bodywork_code: str | None = None
    transmission_id: str | None = None
    transmission_code: str | None = None
    transmission_type_code: str | None = None
    transmission_speeds: int | None = None
    bodywork_name: str | None = None
    bodywork_official_label: str | None = None
    transmission_type_name: str | None = None
    drive_type: str | None = None
    drive_official_label: str | None = None
    drive_code: str | None = None


@dataclass(frozen=True)
class PromotionPreparationSummary:
    promotions: tuple[CanonicalPromotion, ...]
    skipped_by_reason: dict[str, int]
    candidates_written: int


def _year(value: str | None) -> int | None:
    return None if value is None else int(value[:4])


def prepare_canonical_promotions(
    connection: Connection,
    *,
    batch_id: str,
    records: Iterable[TecDocHierarchyRecord],
    engine_fuels: Mapping[str, str],
    engine_fuel_labels: Mapping[str, str] | None = None,
    vehicle_fuels: Mapping[str, str] | None = None,
    bodywork_labels: Mapping[str, str] | None = None,
    bodywork_canonical: Mapping[str, str] | None = None,
    transmission_type_labels: Mapping[str, str] | None = None,
    drive_labels: Mapping[str, str] | None = None,
    drive_canonical: Mapping[str, str] | None = None,
    complete_source: bool = False,
    promotion_limit: int | None = None,
    retain_candidate_only: bool = False,
) -> PromotionPreparationSummary:
    """Persist and return only graph-safe, one-active-engine KType promotions."""

    if promotion_limit is not None and promotion_limit < 1:
        raise ValueError("promotion_limit must be positive")
    materialized = tuple(records)
    observed_displacements: dict[str, set[int]] = {}
    if complete_source:
        for record in materialized:
            if record.displacement_cc is None:
                continue
            for engine in record.engines:
                if not engine.deleted:
                    observed_displacements.setdefault(engine.engine_id, set()).add(
                        record.displacement_cc
                    )
    promotions: list[CanonicalPromotion] = []
    skipped: Counter[str] = Counter()
    candidates_written = 0
    try:
        for record in materialized:
            if promotion_limit is not None and len(promotions) >= promotion_limit:
                break
            active_engines = [engine for engine in record.engines if not engine.deleted]
            vehicle_fuel_type = (vehicle_fuels or {}).get(record.fuel_type_code or "")
            if not active_engines:
                year_from = _year(record.year_from)
                if year_from is None:
                    skipped["year_missing"] += 1
                    if retain_candidate_only:
                        candidates_written += _write_candidate_only(
                            connection, batch_id, record, reason="year_missing",
                            vehicle_fuel_type=vehicle_fuel_type,
                            engine_fuel_labels=engine_fuel_labels,
                        )
                    continue
                candidates = _vehicle_candidates(
                    record,
                    year_from=year_from,
                    engine=None,
                    displacement_cc=record.displacement_cc,
                    displacement_source=("table_120_technical" if record.displacement_cc else None),
                    fuel_type=vehicle_fuel_type,
                    vehicle_fuel_type=vehicle_fuel_type,
                    engine_link_status="allocation_missing",
                    bodywork_labels=bodywork_labels,
                    bodywork_canonical=bodywork_canonical,
                    transmission_type_labels=transmission_type_labels,
                    drive_labels=drive_labels,
                    drive_canonical=drive_canonical,
                )
                ids, written = _write_candidates(connection, batch_id, candidates, record, None)
                candidates_written += written
                promotions.append(
                    _promotion(
                        record,
                        ids,
                        year_from=year_from,
                        engine=None,
                        displacement_cc=record.displacement_cc,
                        displacement_source=(
                            "table_120_technical" if record.displacement_cc else None
                        ),
                        fuel_type=vehicle_fuel_type,
                        engine_link_status="allocation_missing",
                        bodywork_labels=bodywork_labels,
                        bodywork_canonical=bodywork_canonical,
                        transmission_type_labels=transmission_type_labels,
                        drive_labels=drive_labels,
                        drive_canonical=drive_canonical,
                    )
                )
                continue
            if len(active_engines) != 1:
                skipped["engine_ambiguous"] += 1
                if retain_candidate_only:
                    candidates_written += _write_candidate_only(
                        connection, batch_id, record, reason="engine_ambiguous",
                        vehicle_fuel_type=vehicle_fuel_type,
                        engine_fuel_labels=engine_fuel_labels,
                    )
                continue
            engine = active_engines[0]
            fuel_type = engine_fuels.get(engine.fuel_type_code or "")
            if engine_fuel_labels is not None:
                # A caller-supplied scalar cannot flatten explicit mixed or
                # unknown KT088 evidence, nor contradict its single value.
                supported = engine_fuel_evidence(
                    engine.fuel_type_code, engine_fuel_labels,
                ).scalar_fuel_type
                if supported is None or supported != fuel_type:
                    fuel_type = None
            if fuel_type is None:
                skipped["fuel_unresolved"] += 1
                if retain_candidate_only:
                    candidates_written += _write_candidate_only(
                        connection, batch_id, record, reason="fuel_unresolved",
                        vehicle_fuel_type=vehicle_fuel_type,
                        engine_fuel_labels=engine_fuel_labels,
                    )
                continue
            exact_displacement = None
            if (
                engine.displacement_cc_from is not None
                and engine.displacement_cc_from == engine.displacement_cc_to
            ):
                exact_displacement = engine.displacement_cc_from
            corroborated = observed_displacements.get(engine.engine_id, set())
            displacement_cc = exact_displacement
            displacement_source = "table_155_exact"
            if displacement_cc is None and len(corroborated) == 1:
                displacement_cc = next(iter(corroborated))
                displacement_source = "table_120_complete_source_consensus"
            if displacement_cc is None:
                skipped["displacement_unresolved"] += 1
                if retain_candidate_only:
                    candidates_written += _write_candidate_only(
                        connection, batch_id, record, reason="displacement_unresolved",
                        vehicle_fuel_type=vehicle_fuel_type,
                        engine_fuel_labels=engine_fuel_labels,
                    )
                continue
            year_from = _year(record.year_from)
            if year_from is None:
                skipped["year_missing"] += 1
                if retain_candidate_only:
                    candidates_written += _write_candidate_only(
                        connection, batch_id, record, reason="year_missing",
                        vehicle_fuel_type=vehicle_fuel_type,
                        engine_fuel_labels=engine_fuel_labels,
                    )
                continue

            candidates = _vehicle_candidates(
                record,
                year_from=year_from,
                engine=engine,
                displacement_cc=displacement_cc,
                displacement_source=displacement_source,
                fuel_type=fuel_type,
                vehicle_fuel_type=vehicle_fuel_type,
                engine_link_status="linked",
                bodywork_labels=bodywork_labels,
                bodywork_canonical=bodywork_canonical,
                transmission_type_labels=transmission_type_labels,
                drive_labels=drive_labels,
                drive_canonical=drive_canonical,
            )
            ids, written = _write_candidates(connection, batch_id, candidates, record, engine)
            candidates_written += written
            promotions.append(
                _promotion(
                    record,
                    ids,
                    year_from=year_from,
                    engine=engine,
                    displacement_cc=displacement_cc,
                    displacement_source=displacement_source,
                    fuel_type=fuel_type,
                    engine_link_status="linked",
                    bodywork_labels=bodywork_labels,
                    bodywork_canonical=bodywork_canonical,
                    transmission_type_labels=transmission_type_labels,
                    drive_labels=drive_labels,
                    drive_canonical=drive_canonical,
                )
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return PromotionPreparationSummary(tuple(promotions), dict(skipped), candidates_written)


def _candidate_only_vehicle_candidates(
    record: TecDocHierarchyRecord,
    *,
    reason: str,
    vehicle_fuel_type: str | None = None,
    engine_fuel_labels: Mapping[str, str] | None = None,
) -> tuple[CanonicalCandidate, ...]:
    """Retain an active KType for matching without making it graph-promotable."""

    return (
        CanonicalCandidate(
            "manufacturer",
            f"manufacturer:{record.manufacturer_id}",
            {"canonical_name": record.manufacturer_name},
        ),
        CanonicalCandidate(
            "model_family",
            f"model:{record.model_id}",
            {
                "canonical_name": record.model_name,
                "manufacturer_source_key": f"manufacturer:{record.manufacturer_id}",
            },
        ),
        CanonicalCandidate(
            "vehicle_variant",
            f"variant:{record.ktype_id}",
            {
                "market": [],
                "year_from": _year(record.year_from),
                "year_to": _year(record.year_to),
                "source_name": record.ktype_name,
                "manufacturer_source_key": f"manufacturer:{record.manufacturer_id}",
                "model_family_source_key": f"model:{record.model_id}",
                "engine_link_status": (
                    "ambiguous" if reason == "engine_ambiguous" else "review_required"
                ),
                "promotion_status": "candidate_only",
                "candidate_only_reason": reason,
                "engine_fuel_evidence": [
                    {
                        "engine_source_key": f"engine:{engine.engine_id}",
                        "engine_source_row_ref": engine.engine_source_row_ref,
                        "engine_deleted": engine.deleted,
                        "fuel": engine_fuel_evidence(
                            engine.fuel_type_code, engine_fuel_labels or {},
                        ).as_attributes(),
                    }
                    for engine in record.engines
                ],
                "power_kw": record.power_kw,
                "displacement_cc": record.displacement_cc,
                "tecdoc_fuel_code": record.fuel_type_code,
                "vehicle_fuel_type": vehicle_fuel_type,
                "tecdoc_engine_type_code": record.engine_type_code,
                "tecdoc_drive_type_code": record.drive_type_code,
                "tecdoc_transmission_type_code": record.transmission_type_code,
                "tecdoc_body_type_code": record.body_type_code,
                "hierarchy_link_status": "model_family_linked_platform_optional",
            },
        ),
        CanonicalCandidate(
            "alias",
            f"ktype:{record.ktype_id}",
            {
                "alias_text": record.ktype_id,
                "alias_type": "k_type",
                "source_system": "tecdoc",
                "target_source_key": f"variant:{record.ktype_id}",
                "promotion_status": "candidate_only",
                "candidate_only_reason": reason,
            },
        ),
    )


def _write_candidate_only(
    connection: Connection,
    batch_id: str,
    record: TecDocHierarchyRecord,
    *,
    reason: str,
    vehicle_fuel_type: str | None,
    engine_fuel_labels: Mapping[str, str] | None = None,
) -> int:
    _, written = _write_candidates(
        connection,
        batch_id,
        _candidate_only_vehicle_candidates(
            record, reason=reason, vehicle_fuel_type=vehicle_fuel_type,
            engine_fuel_labels=engine_fuel_labels,
        ),
        record,
        None,
    )
    return written


def _vehicle_candidates(
    record: TecDocHierarchyRecord,
    *,
    year_from: int,
    engine: EngineAllocation | None,
    displacement_cc: int | None,
    displacement_source: str | None,
    fuel_type: str | None,
    vehicle_fuel_type: str | None,
    engine_link_status: str,
    bodywork_labels: Mapping[str, str] | None,
    bodywork_canonical: Mapping[str, str] | None,
    transmission_type_labels: Mapping[str, str] | None,
    drive_labels: Mapping[str, str] | None,
    drive_canonical: Mapping[str, str] | None,
) -> tuple[CanonicalCandidate, ...]:
    engine_source_key = f"engine:{engine.engine_id}" if engine is not None else None
    transmission = _resolved_transmission(record)
    transmission_source_key = (
        f"transmission:{transmission.transmission_id}" if transmission else None
    )
    canonical_bodywork = (bodywork_canonical or {}).get(record.body_type_code or "")
    bodywork_source_key = (
        f"bodywork:tecdoc-086:{record.body_type_code}"
        if canonical_bodywork
        else None
    )
    bodywork_name = (bodywork_labels or {}).get(record.body_type_code or "")
    transmission_type_name = (transmission_type_labels or {}).get(
        transmission.transmission_type_code or "" if transmission else ""
    )
    drive_type = (drive_canonical or {}).get(record.drive_type_code or "")
    drive_official_label = (drive_labels or {}).get(record.drive_type_code or "")
    candidates = [
        CanonicalCandidate(
            "manufacturer",
            f"manufacturer:{record.manufacturer_id}",
            {"canonical_name": record.manufacturer_name},
        ),
        CanonicalCandidate(
            "model_family",
            f"model:{record.model_id}",
            {
                "canonical_name": record.model_name,
                "manufacturer_source_key": f"manufacturer:{record.manufacturer_id}",
            },
        ),
        CanonicalCandidate(
            "vehicle_variant",
            f"variant:{record.ktype_id}",
            {
                "market": [],
                "year_from": year_from,
                "year_to": _year(record.year_to),
                "source_name": record.ktype_name,
                "manufacturer_source_key": f"manufacturer:{record.manufacturer_id}",
                "model_family_source_key": f"model:{record.model_id}",
                **({"engine_source_key": engine_source_key} if engine_source_key else {}),
                **(
                    {"transmission_source_key": transmission_source_key}
                    if transmission_source_key
                    else {}
                ),
                **({"bodywork_source_key": bodywork_source_key} if bodywork_source_key else {}),
                "engine_link_status": engine_link_status,
                "transmission_link_status": (
                    "linked"
                    if transmission
                    else "ambiguous" if record.transmissions else "allocation_missing"
                ),
                "bodywork_link_status": (
                    "linked"
                    if bodywork_source_key
                    else "review_required" if record.body_type_code else "code_missing"
                ),
                "bodywork_normalization_status": (
                    "mapped" if canonical_bodywork else "review_required"
                ),
                "tecdoc_bodywork_official_label": bodywork_name,
                "drive_type": drive_type,
                "tecdoc_drive_type_code": record.drive_type_code,
                "tecdoc_drive_official_label": drive_official_label,
                "drive_normalization_status": (
                    "mapped" if drive_type else "review_required"
                ),
                "tecdoc_fuel_code": record.fuel_type_code,
                "tecdoc_engine_type_code": record.engine_type_code,
                "tecdoc_transmission_type_code": record.transmission_type_code,
                "tecdoc_body_type_code": record.body_type_code,
                "power_kw": record.power_kw,
                "displacement_cc": displacement_cc,
                "displacement_source": displacement_source,
                "fuel_type": fuel_type,
                "vehicle_fuel_type": vehicle_fuel_type,
                "hierarchy_link_status": "model_family_linked_platform_optional",
            },
        ),
    ]
    if engine is not None:
        candidates.append(
            CanonicalCandidate(
                "engine",
                engine_source_key or "",
                {
                    "engine_code": engine.engine_code,
                    "displacement_cc": displacement_cc,
                    "displacement_source": displacement_source,
                    "fuel_type": fuel_type,
                },
            )
        )
    if transmission is not None:
        candidates.append(
            CanonicalCandidate(
                "transmission",
                transmission_source_key or "",
                {
                    "transmission_code": transmission.transmission_code,
                    "tecdoc_transmission_type_code": transmission.transmission_type_code,
                    "transmission_type_name": transmission_type_name,
                    "transmission_identity": transmission.transmission_identity,
                    "speeds": transmission.speeds,
                },
            )
        )
    if bodywork_source_key is not None:
        candidates.append(
            CanonicalCandidate(
                "bodywork",
                bodywork_source_key,
                {
                    "canonical_name": canonical_bodywork,
                    "official_label": bodywork_name,
                    "tecdoc_body_type_code": record.body_type_code,
                    "terminology_status": "canonical_mapped_from_official_english",
                },
            )
        )
    candidates.append(
        CanonicalCandidate(
            "alias",
            f"ktype:{record.ktype_id}",
            {
                "alias_text": record.ktype_id,
                "alias_type": "k_type",
                "source_system": "tecdoc",
                "target_source_key": f"variant:{record.ktype_id}",
            },
        )
    )
    return tuple(candidates)


def _write_candidates(
    connection: Connection,
    batch_id: str,
    candidates: tuple[CanonicalCandidate, ...],
    record: TecDocHierarchyRecord,
    engine: EngineAllocation | None,
) -> tuple[dict[str, str], int]:
    ids: dict[str, str] = {}
    written = 0
    for candidate in candidates:
        node_id = get_or_mint_node_id(connection, candidate)
        ids[candidate.entity_type] = node_id
        source_refs: tuple[str, ...] = record.source_row_refs
        if candidate.entity_type == "engine" and engine is not None:
            source_refs = (*source_refs, engine.engine_source_row_ref)
        if candidate.entity_type == "transmission":
            transmission = _resolved_transmission(record)
            if transmission is not None:
                source_refs = (
                    *source_refs,
                    transmission.transmission_source_row_ref,
                    *(item.source_row_ref for item in transmission.applicability),
                )
        if write_candidate(
            connection,
            batch_id=batch_id,
            candidate=candidate,
            node_id=node_id,
            source_row_refs=source_refs,
        ):
            written += 1
    return ids, written


def _promotion(
    record: TecDocHierarchyRecord,
    ids: dict[str, str],
    *,
    year_from: int,
    engine: EngineAllocation | None,
    displacement_cc: int | None,
    displacement_source: str | None,
    fuel_type: str | None,
    engine_link_status: str,
    bodywork_labels: Mapping[str, str] | None,
    bodywork_canonical: Mapping[str, str] | None,
    transmission_type_labels: Mapping[str, str] | None,
    drive_labels: Mapping[str, str] | None,
    drive_canonical: Mapping[str, str] | None,
) -> CanonicalPromotion:
    assertion_key = f"ktype:{record.ktype_id}"
    transmission = _resolved_transmission(record)
    bodywork_name = (bodywork_labels or {}).get(record.body_type_code or "")
    canonical_bodywork = (bodywork_canonical or {}).get(record.body_type_code or "")
    transmission_type_name = (transmission_type_labels or {}).get(
        transmission.transmission_type_code or "" if transmission else ""
    )
    drive_type = (drive_canonical or {}).get(record.drive_type_code or "")
    drive_official_label = (drive_labels or {}).get(record.drive_type_code or "")
    return CanonicalPromotion(
        manufacturer_id=ids["manufacturer"],
        manufacturer_name=record.manufacturer_name,
        model_family_id=ids["model_family"],
        model_family_name=record.model_name,
        variant_id=ids["vehicle_variant"],
        year_from=year_from,
        year_to=_year(record.year_to),
        engine_id=ids.get("engine"),
        engine_code=(engine.engine_code if engine is not None else None),
        displacement_cc=displacement_cc,
        displacement_source=displacement_source,
        fuel_type=fuel_type,
        tecdoc_fuel_code=record.fuel_type_code,
        tecdoc_engine_type_code=record.engine_type_code,
        engine_link_status=engine_link_status,
        power_kw=record.power_kw,
        alias_id=ids["alias"],
        alias_text=record.ktype_id,
        source_record_key=f"ktype:{record.ktype_id}",
        source_assertion_key=assertion_key,
        assertion_identity=build_assertion_identity("tecdoc", assertion_key),
        bodywork_id=ids.get("bodywork"),
        bodywork_code=record.body_type_code,
        transmission_id=ids.get("transmission"),
        transmission_code=(transmission.transmission_code if transmission else None),
        transmission_type_code=(
            transmission.transmission_type_code if transmission else None
        ),
        transmission_speeds=(transmission.speeds if transmission else None),
        bodywork_name=canonical_bodywork,
        bodywork_official_label=bodywork_name,
        transmission_type_name=transmission_type_name,
        drive_type=drive_type,
        drive_official_label=drive_official_label,
        drive_code=record.drive_type_code,
    )


def _resolved_transmission(record: TecDocHierarchyRecord) -> TransmissionAllocation | None:
    """Return a transmission only when Table 547 resolves one distinct Table 544 row."""

    return record.transmissions[0] if len(record.transmissions) == 1 else None
