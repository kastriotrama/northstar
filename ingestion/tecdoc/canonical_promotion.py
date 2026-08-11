"""Prepare safe canonical graph promotions from real TecDoc hierarchy records."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from psycopg import Connection

from ingestion.tecdoc.dat_extraction import TecDocHierarchyRecord
from ingestion.tecdoc.models import CanonicalCandidate
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
    engine_id: str
    engine_code: str
    displacement_cc: int
    displacement_source: str
    fuel_type: str
    power_kw: int | None
    alias_id: str
    alias_text: str
    source_record_key: str
    source_assertion_key: str
    assertion_identity: str


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
    complete_source: bool = False,
    promotion_limit: int | None = None,
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
            if not active_engines:
                skipped["engine_missing"] += 1
                continue
            if len(active_engines) != 1:
                skipped["engine_ambiguous"] += 1
                continue
            engine = active_engines[0]
            fuel_type = engine_fuels.get(engine.fuel_type_code or "")
            if fuel_type is None:
                skipped["fuel_unresolved"] += 1
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
                continue
            year_from = _year(record.year_from)
            if year_from is None:
                skipped["year_missing"] += 1
                continue

            candidates = (
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
                        "engine_source_key": f"engine:{engine.engine_id}",
                        "hierarchy_link_status": "awaiting_platform_mapping",
                    },
                ),
                CanonicalCandidate(
                    "engine",
                    f"engine:{engine.engine_id}",
                    {
                        "engine_code": engine.engine_code,
                        "displacement_cc": displacement_cc,
                        "displacement_source": displacement_source,
                        "fuel_type": fuel_type,
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
                    },
                ),
            )
            ids: dict[str, str] = {}
            for candidate in candidates:
                node_id = get_or_mint_node_id(connection, candidate)
                ids[candidate.entity_type] = node_id
                source_refs = (
                    (*record.source_row_refs, engine.engine_source_row_ref)
                    if candidate.entity_type == "engine"
                    else record.source_row_refs
                )
                if write_candidate(
                    connection,
                    batch_id=batch_id,
                    candidate=candidate,
                    node_id=node_id,
                    source_row_refs=source_refs,
                ):
                    candidates_written += 1
            assertion_key = f"ktype:{record.ktype_id}"
            promotions.append(
                CanonicalPromotion(
                    manufacturer_id=ids["manufacturer"],
                    manufacturer_name=record.manufacturer_name,
                    model_family_id=ids["model_family"],
                    model_family_name=record.model_name,
                    variant_id=ids["vehicle_variant"],
                    year_from=year_from,
                    year_to=_year(record.year_to),
                    engine_id=ids["engine"],
                    engine_code=engine.engine_code,
                    displacement_cc=displacement_cc,
                    displacement_source=displacement_source,
                    fuel_type=fuel_type,
                    power_kw=record.power_kw,
                    alias_id=ids["alias"],
                    alias_text=record.ktype_id,
                    source_record_key=f"ktype:{record.ktype_id}",
                    source_assertion_key=assertion_key,
                    assertion_identity=build_assertion_identity("tecdoc", assertion_key),
                )
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return PromotionPreparationSummary(tuple(promotions), dict(skipped), candidates_written)
