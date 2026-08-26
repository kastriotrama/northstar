"""Sample a sanitized, plate-stratified adjudication set for margin calibration.

The margin gate (`minimum_candidate_margin`) was calibrated against clamped
confidence margins. Ranking and routing now use unclamped separation scores,
which occupy a wider scale, so the stored threshold no longer expresses the
strictness it was chosen for. No accepted TS-to-KType decision exists anywhere
in the system, so the threshold cannot be refitted without first building a
labelled set.

This script draws competitive top-vs-runner-up pairs from across the whole
plate space, stratifies them by separation margin so the decision boundary is
densely covered, and enqueues them for human adjudication. It writes nothing
unless `--commit` is passed, and it never retains plates, VINs or raw rows.
"""

from __future__ import annotations

import argparse
import json
import os
import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid5

import psycopg
from neo4j import GraphDatabase
from psycopg.rows import dict_row

from ingestion.active_rules import load_active_rules
from ingestion.fuzzy_matching import (
    FuzzyCandidateMatch,
    FuzzyMatchConfig,
    FuzzyVehicleMatcher,
    ManufacturerCandidateIndex,
    VehicleMatchQuery,
)
from ingestion.normalization_rules import normalize_ts_record
from ingestion.review_queue import CandidateMatch, enqueue_review_item
from ingestion.tecdoc.manufacturer_mapping import TecDocManufacturerIndex
from ingestion.tecdoc.match_run_adapters import load_ktype_catalog
from ingestion.tecdoc.remote_match_run import PASSENGER_FILTER_SQL

SOURCE_SYSTEM = "Transportstyrelsen"
SOURCE_TABLE = "staging.transportstyrelsen_raw"
REASON_CODE = "match_margin_calibration"
TARGET_ENTITY_TYPE = "vehicle"

# Stable namespace so re-running the same seed and pins is idempotent.
CALIBRATION_NAMESPACE = UUID("6f0a5f4e-9d2c-4a3b-8e1f-2c7d9b6a4e30")

EVIDENCE_FIELDS = ("brand", "variant", "version", "model_no", "type_text", "eeg_type_approval")

# Bands straddle the decision boundary on the separation scale. Equal sampling
# per band buys resolution where the threshold sits, which a population-
# representative sample would not.
MARGIN_BANDS: tuple[tuple[float, float], ...] = (
    (0.00, 0.05),
    (0.05, 0.10),
    (0.10, 0.15),
    (0.15, 0.20),
    (0.20, 0.25),
    (0.25, 0.30),
    (0.30, 0.40),
    (0.40, 1.00),
)

PLATE_LETTERS = "ABCDEFGHJKLMNOPRSTUWXYZ"


@dataclass(frozen=True)
class CalibrationItem:
    """One sanitized top-vs-runner-up pair awaiting human adjudication."""

    band: str
    separation_margin: float
    confidence_margin: float
    scope: str
    source_evidence: dict[str, Any]
    top: FuzzyCandidateMatch
    runner_up: FuzzyCandidateMatch


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _integer(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) and int(value) > 0 else None


def _text(value: object) -> str | None:
    return str(value) if value else None


def _band_label(margin: float) -> str | None:
    for low, high in MARGIN_BANDS:
        if low <= margin < high:
            return f"{low:.2f}-{high:.2f}"
    return None


def _band_bounds(label: str) -> tuple[float, float]:
    low, high = label.split("-", maxsplit=1)
    return float(low), float(high)


def _seek_keys(count: int, rng: random.Random) -> tuple[str, ...]:
    """Random three-letter keys spread across the observed plate space."""

    keys = {
        "".join(rng.choice(PLATE_LETTERS) for _ in range(3)) for _ in range(count * 2)
    }
    return tuple(sorted(keys))[:count]


def _fetch_scattered_rows(
    remote: psycopg.Connection, keys: tuple[str, ...]
) -> tuple[dict[str, Any], ...]:
    """Seek the first passenger row at or after each key, using the plate index."""

    with remote.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT r.* FROM unnest(%s::text[]) AS k(key) "
            "CROSS JOIN LATERAL ("
            f"  SELECT * FROM public.swedish_vehicles WHERE {PASSENGER_FILTER_SQL} "
            "   AND plate >= k.key ORDER BY plate LIMIT 1"
            ") AS r",
            (list(keys),),
        )
        rows = [dict(row) for row in cursor.fetchall()]
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        unique.setdefault(str(row["plate"]), row)
    return tuple(unique[plate] for plate in sorted(unique))


def _candidate_payload(candidate: FuzzyCandidateMatch) -> dict[str, Any]:
    return {
        "manufacturer": candidate.manufacturer,
        "model": candidate.model,
        "matched_label": candidate.matched_label,
        "separation_score": candidate.separation_score,
        "text_score": candidate.text_score,
        "context_effect": candidate.context_effect,
        "matched_fields": list(candidate.matched_fields),
        "missing_fields": list(candidate.missing_fields),
        "conflicting_fields": list(candidate.conflicting_fields),
    }


def collect_items(
    rows: tuple[dict[str, Any], ...],
    *,
    rules: Any,
    manufacturer_rules: Any,
    index: ManufacturerCandidateIndex,
    matcher: FuzzyVehicleMatcher,
    bridge: TecDocManufacturerIndex,
    config: FuzzyMatchConfig,
) -> list[CalibrationItem]:
    items: list[CalibrationItem] = []
    for raw in rows:
        outcome = normalize_ts_record(
            raw, rule_set=rules, manufacturer_entity_rules=manufacturer_rules
        )
        if outcome.status in {"failed", "review_required"}:
            continue
        normalized = _mapping(outcome.normalized)
        if normalized.get("record_route") in {
            "exclude_from_passenger_car_dataset",
            "quarantine_test_record",
        }:
            continue
        candidates_map = _mapping(outcome.candidates)
        manufacturer = normalized.get("manufacturer") or candidates_map.get("manufacturer")
        model = normalized.get("model_family") or candidates_map.get("model_family")
        if not manufacturer:
            continue
        evidence = {
            field: str(value)
            for field in EVIDENCE_FIELDS
            if (value := raw.get(field))
        }
        if not model and evidence:
            recovered = index.recover_model_from_evidence(str(manufacturer), evidence)
            if recovered is not None:
                model, _ = recovered
        if not model:
            continue
        energy = normalized.get("energy_sources")
        fuels = (
            frozenset(str(value) for value in energy)
            if isinstance(energy, list)
            else frozenset()
        )
        decision = bridge.resolve(manufacturer=manufacturer, brand=raw.get("brand"))
        scoped = (
            decision.manufacturer
            if decision.status == "resolved" and decision.manufacturer
            else str(manufacturer)
        )
        try:
            query = VehicleMatchQuery(
                manufacturer=scoped,
                model=str(model),
                year=_integer(normalized.get("production_year")),
                fuels=fuels,
                engine_code=_text(normalized.get("engine_code")),
                displacement_cc=_integer(normalized.get("displacement_cc")),
                power_kw=_integer(normalized.get("power_kw")),
                drive_type=_text(normalized.get("drive_type")),
                bodywork=_text(normalized.get("bodywork_form")),
            )
        except ValueError:
            continue
        _, scope = index.lookup(
            query.manufacturer, similarity_threshold=config.manufacturer_scope_threshold
        )
        if scope == "global":
            continue
        result = matcher.match(query)
        if len(result.candidates) < 2:
            continue
        top, runner_up = result.candidates[0], result.candidates[1]
        separation = max(0.0, top.separation_score - runner_up.separation_score)
        band = _band_label(separation)
        if band is None:
            continue
        items.append(
            CalibrationItem(
                band=band,
                separation_margin=round(separation, 6),
                confidence_margin=round(max(0.0, top.confidence - runner_up.confidence), 6),
                scope=result.scope,
                source_evidence={
                    "manufacturer": str(manufacturer),
                    "model": str(model),
                    "production_year": query.year,
                    "energy_sources": sorted(fuels),
                    "engine_code": query.engine_code,
                    "displacement_cc": query.displacement_cc,
                    "power_kw": query.power_kw,
                    "drive_type": query.drive_type,
                    "bodywork_form": query.bodywork,
                },
                top=top,
                runner_up=runner_up,
            )
        )
    return items


def select_stratified(
    items: list[CalibrationItem],
    per_band: int,
    rng: random.Random,
    *,
    min_margin: float = 0.0,
    max_margin: float = 1.0,
) -> list[CalibrationItem]:
    by_band: dict[str, list[CalibrationItem]] = defaultdict(list)
    for item in items:
        band_low, band_high = _band_bounds(item.band)
        if band_high <= min_margin or band_low >= max_margin:
            continue
        if item.separation_margin < min_margin or item.separation_margin >= max_margin:
            continue
        by_band[item.band].append(item)
    selected: list[CalibrationItem] = []
    for low, high in MARGIN_BANDS:
        band = f"{low:.2f}-{high:.2f}"
        pool = sorted(by_band.get(band, ()), key=lambda entry: entry.separation_margin)
        if len(pool) <= per_band:
            selected.extend(pool)
            continue
        selected.extend(rng.sample(pool, per_band))
    return sorted(selected, key=lambda entry: (entry.band, entry.separation_margin))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-version", required=True)
    parser.add_argument("--normalization-rule-version", required=True)
    parser.add_argument("--candidate-catalog-version", required=True)
    parser.add_argument("--expected-ktype-count", type=int, required=True)
    parser.add_argument("--batch-label", required=True, help="Filterable review batch id.")
    parser.add_argument("--per-band", type=int, default=25)
    parser.add_argument("--seek-keys", type=int, default=6000)
    parser.add_argument("--seed", type=int, default=20260824)
    parser.add_argument(
        "--min-margin",
        type=float,
        default=0.0,
        help="Only select competitive pairs whose separation margin is at least this value.",
    )
    parser.add_argument(
        "--max-margin",
        type=float,
        default=1.0,
        help="Only select competitive pairs whose separation margin is below this value.",
    )
    parser.add_argument(
        "--weights-out",
        help=(
            "Write the per-band population histogram of competitive pairs here. "
            "The sample is stratified, so fitting a threshold requires these "
            "weights to recover population-level rates."
        ),
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Write the sampled items to core.review_queue. Omitted means dry run.",
    )
    args = parser.parse_args()
    if not 0.0 <= args.min_margin < args.max_margin <= 1.0:
        raise ValueError("--min-margin and --max-margin must satisfy 0.0 <= min < max <= 1.0")

    rng = random.Random(args.seed)

    with (
        psycopg.connect(os.environ["DATABASE_URL"]) as local,
        psycopg.connect(os.environ["REMOTE_DATABASE_URL"]) as remote,
        GraphDatabase.driver(
            os.environ["NEO4J_URI"],
            auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
        ) as driver,
    ):
        rules, manufacturer_rules = load_active_rules(local)
        if rules.version != args.normalization_rule_version:
            raise ValueError("active normalization rule version differs from pinned version")
        catalog = load_ktype_catalog(driver)
        if len(catalog) != args.expected_ktype_count:
            raise ValueError(
                f"TecDoc KType count mismatch: expected {args.expected_ktype_count}, "
                f"found {len(catalog)}"
            )
        config = FuzzyMatchConfig()
        index = ManufacturerCandidateIndex(catalog)
        matcher = FuzzyVehicleMatcher(index, config)
        bridge = TecDocManufacturerIndex(
            sorted({candidate.manufacturer for candidate in catalog}), manufacturer_rules or {}
        )
        rows = _fetch_scattered_rows(remote, _seek_keys(args.seek_keys, rng))
        items = collect_items(
            rows,
            rules=rules,
            manufacturer_rules=manufacturer_rules,
            index=index,
            matcher=matcher,
            bridge=bridge,
            config=config,
        )
        selected = select_stratified(
            items,
            args.per_band,
            rng,
            min_margin=args.min_margin,
            max_margin=args.max_margin,
        )

        band_counts = {
            f"{low:.2f}-{high:.2f}": sum(
                1 for item in selected if item.band == f"{low:.2f}-{high:.2f}"
            )
            for low, high in MARGIN_BANDS
        }
        population_counts = {
            f"{low:.2f}-{high:.2f}": sum(
                1 for item in items if item.band == f"{low:.2f}-{high:.2f}"
            )
            for low, high in MARGIN_BANDS
        }
        summary = {
            "sampled_rows": len(rows),
            "competitive_pairs": len(items),
            "selected": len(selected),
            "per_band_selected": band_counts,
            "per_band_population": population_counts,
            "min_margin": args.min_margin,
            "max_margin": args.max_margin,
            "committed": bool(args.commit),
        }

        if args.weights_out:
            with open(args.weights_out, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "batch_label": args.batch_label,
                        "seed": args.seed,
                        "seek_keys": args.seek_keys,
                        "sampled_rows": len(rows),
                        "competitive_pairs": len(items),
                        "min_margin": args.min_margin,
                        "max_margin": args.max_margin,
                        "per_band_population": population_counts,
                        "per_band_selected": band_counts,
                        "pins": {
                            "source_version": args.source_version,
                            "normalization_rule_version": args.normalization_rule_version,
                            "candidate_catalog_version": args.candidate_catalog_version,
                        },
                    },
                    handle,
                    indent=2,
                    sort_keys=True,
                )

        if args.commit:
            written = 0
            for ordinal, item in enumerate(selected, start=1):
                review_id = uuid5(
                    CALIBRATION_NAMESPACE,
                    f"{args.batch_label}|{args.source_version}|{ordinal}",
                )
                enqueue_review_item(
                    local,
                    review_id=review_id,
                    source_system=SOURCE_SYSTEM,
                    source_table=SOURCE_TABLE,
                    source_record_id=ordinal,
                    source_batch_id=args.batch_label,
                    reason_code=REASON_CODE,
                    reason_detail=json.dumps(
                        {
                            "question": (
                                "Is the top candidate the correct TecDoc KType for this "
                                "vehicle? Answer accept, reject, or unsure."
                            ),
                            "band": item.band,
                            "separation_margin": item.separation_margin,
                            "confidence_margin": item.confidence_margin,
                            "match_scope": item.scope,
                            "source_evidence": item.source_evidence,
                            "pins": {
                                "source_version": args.source_version,
                                "normalization_rule_version": args.normalization_rule_version,
                                "candidate_catalog_version": args.candidate_catalog_version,
                                "seed": args.seed,
                            },
                        },
                        sort_keys=True,
                    ),
                    target_entity_type=TARGET_ENTITY_TYPE,
                    confidence=item.top.confidence,
                    candidate_matches=(
                        CandidateMatch(
                            candidate_reference=item.top.candidate_reference,
                            candidate_type=item.top.candidate_type,
                            confidence=item.top.confidence,
                            evidence={"rank": "top", **_candidate_payload(item.top)},
                        ),
                        CandidateMatch(
                            candidate_reference=item.runner_up.candidate_reference,
                            candidate_type=item.runner_up.candidate_type,
                            confidence=item.runner_up.confidence,
                            evidence={"rank": "runner_up", **_candidate_payload(item.runner_up)},
                        ),
                    ),
                )
                written += 1
            local.commit()
            summary["written"] = written

    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
