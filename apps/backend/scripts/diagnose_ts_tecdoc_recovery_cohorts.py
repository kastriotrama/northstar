#!/usr/bin/env python3
"""Profile model-missing and bodywork-conflict cohorts without source identity."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

import psycopg

from ingestion.active_rules import load_active_rules
from ingestion.match_run_service import MatchSourceRecord
from ingestion.normalization_rules import normalize_ts_record
from ingestion.tecdoc.match_diagnostics import (
    BodyworkConflictDiagnostics,
    BodyworkConflictObservation,
)
from ingestion.tecdoc.match_run_adapters import (
    TecDocDryRunEvaluator,
    load_postgres_ktype_catalog,
)
from ingestion.tecdoc.model_aliases import ReviewedModelAliasIndex
from ingestion.tecdoc.model_fingerprint_proposals import (
    MODEL_FINGERPRINT_PROFILES,
    ModelFingerprintObservation,
    project_model_fingerprint,
    propose_model_fingerprints,
)

SOURCE_EVIDENCE_FIELDS = (
    "brand",
    "model",
    "variant",
    "version",
    "model_no",
    "type_text",
    "eeg_type_approval",
)


def source_evidence_profile(raw: dict[str, Any]) -> str:
    """Return field-presence only; values and vehicle identity are excluded."""

    populated = tuple(
        field for field in SOURCE_EVIDENCE_FIELDS if str(raw.get(field) or "").strip()
    )
    return "+".join(populated) if populated else "none"


def _integer(value: object) -> int | None:
    try:
        parsed = int(float(str(value)))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _text(value: object) -> str:
    return str(value or "").strip()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-batch-prefix", required=True)
    parser.add_argument("--candidate-catalog-version", required=True)
    parser.add_argument("--limit", type=int, default=20_000)
    parser.add_argument("--after-id", type=int, default=0)
    parser.add_argument("--minimum-anchor-count", type=int, default=2)
    parser.add_argument("--report-limit", type=int, default=100)
    parser.add_argument(
        "--model-only",
        action="store_true",
        help="Skip matcher routes and bodywork diagnostics when profiling model evidence.",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.limit < 1 or args.report_limit < 1 or args.minimum_anchor_count < 1:
        raise SystemExit("limit, report-limit and minimum-anchor-count must be positive")
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    observations: list[ModelFingerprintObservation] = []
    missing_profiles: Counter[str] = Counter()
    bodywork = BodyworkConflictDiagnostics()
    terminal_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()

    with psycopg.connect(database_url) as connection:
        rule_set, manufacturer_rules = load_active_rules(connection)
        catalog = load_postgres_ktype_catalog(connection, batch_id=args.candidate_catalog_version)
        evaluator = (
            None
            if args.model_only
            else TecDocDryRunEvaluator(
                catalog, manufacturer_rules, ReviewedModelAliasIndex(rule_set)
            )
        )
        candidate_by_reference = {candidate.candidate_reference: candidate for candidate in catalog}
        allowed_models: dict[str, set[str]] = defaultdict(set)
        for candidate in catalog:
            allowed_models[candidate.manufacturer].add(candidate.model)
            allowed_models[candidate.manufacturer].update(candidate.model_aliases)

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, raw_record FROM staging.transportstyrelsen_raw "
                "WHERE source_batch_id LIKE %s AND id > %s ORDER BY id LIMIT %s",
                (f"{args.source_batch_prefix}%", args.after_id, args.limit),
            )
            rows = tuple(cursor.fetchall())

        for source_id, raw_value in rows:
            raw = dict(raw_value)
            outcome = normalize_ts_record(
                raw,
                rule_set=rule_set,
                manufacturer_entity_rules=manufacturer_rules,
            )
            source_evidence = {field: raw.get(field) for field in SOURCE_EVIDENCE_FIELDS}
            record = MatchSourceRecord(
                int(source_id),
                {
                    "normalization_status": outcome.status,
                    "normalized": outcome.normalized,
                    "candidates": outcome.candidates,
                    "review_reasons": list(outcome.review_reasons),
                    "source_evidence": source_evidence,
                },
            )
            evaluation = evaluator.evaluate(record) if evaluator is not None else None
            if evaluation is not None:
                terminal_counts[evaluation.terminal] += 1
                reason_counts.update(evaluation.reason_codes)

            normalized = dict(outcome.normalized)
            candidates = dict(outcome.candidates)
            manufacturer = _text(normalized.get("manufacturer") or candidates.get("manufacturer"))
            model = _text(
                normalized.get("model_family") or candidates.get("model_family") or raw.get("model")
            )
            if manufacturer:
                energy = normalized.get("energy_sources")
                fuels = sorted(str(value) for value in energy) if isinstance(energy, list) else []
                observations.append(
                    ModelFingerprintObservation(
                        manufacturer=manufacturer,
                        type_text=_text(raw.get("type_text")),
                        type_approval=_text(raw.get("eeg_type_approval")),
                        variant=_text(raw.get("variant")),
                        version=_text(raw.get("version")),
                        production_year=_integer(normalized.get("production_year")),
                        fuel="+".join(fuels),
                        displacement_cc=_integer(normalized.get("displacement_cc")),
                        power_kw=_integer(normalized.get("power_kw")),
                        model=model or None,
                    )
                )
            model_is_missing = not model
            if model_is_missing:
                missing_profiles[source_evidence_profile(raw)] += 1
            if (
                evaluation is not None
                and "context_conflict:bodywork" in evaluation.reason_codes
                and evaluation.top_candidate_reference
                and (
                    top_candidate := candidate_by_reference.get(evaluation.top_candidate_reference)
                )
                and (ts_bodywork := _text(normalized.get("bodywork_form")))
                and top_candidate.bodyworks
            ):
                bodywork.add(
                    BodyworkConflictObservation(
                        manufacturer=top_candidate.manufacturer,
                        model=top_candidate.model,
                        ts_bodywork_code=_text(raw.get("body_code")),
                        ts_bodywork=ts_bodywork,
                        tecdoc_bodyworks=tuple(top_candidate.bodyworks),
                    )
                )

    proposals_by_profile = {
        profile: propose_model_fingerprints(
            tuple(
                project_model_fingerprint(observation, profile=profile)
                for observation in observations
            ),
            allowed_models_by_manufacturer=allowed_models,
            minimum_anchor_count=args.minimum_anchor_count,
        )
        for profile in MODEL_FINGERPRINT_PROFILES
    }
    payload = {
        "mode": "privacy_safe_diagnostics",
        "source_batch_prefix": args.source_batch_prefix,
        "candidate_catalog_version": args.candidate_catalog_version,
        "rule_version": rule_set.version,
        "after_id": args.after_id,
        "processed": len(rows),
        "last_source_id": int(rows[-1][0]) if rows else args.after_id,
        "terminal_counts": dict(sorted(terminal_counts.items())),
        "model_evidence_missing": sum(missing_profiles.values()),
        "missing_model_source_profiles": dict(
            sorted(missing_profiles.items(), key=lambda item: (-item[1], item[0]))
        ),
        "catalog_gated_model_proposal_count": sum(
            len(proposals) for proposals in proposals_by_profile.values()
        ),
        "catalog_gated_model_proposals_by_profile": {
            profile: [asdict(proposal) for proposal in proposals]
            for profile, proposals in proposals_by_profile.items()
        },
        "bodywork_conflict_count": reason_counts["context_conflict:bodywork"],
        "bodywork_conflict_groups": list(bodywork.report(limit=args.report_limit)),
        "bodywork_compatibility_proposals": list(bodywork.compatibility_proposals()),
        "privacy": "No plate, VIN, source ID, or raw source payload is included.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                key: payload[key]
                for key in (
                    "processed",
                    "model_evidence_missing",
                    "catalog_gated_model_proposal_count",
                    "bodywork_conflict_count",
                    "last_source_id",
                )
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
