"""Plate-free, exhaustive matcher-pattern aggregation for audit runs."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from ingestion.match_run_migrations import (
    MATCH_RUN_PATTERN_BATCHES_TABLE,
    MATCH_RUN_PATTERN_INVENTORY_TABLE,
    MATCH_RUN_PATTERN_MEMBERS_TABLE,
)
from ingestion.tecdoc.blocker_review import MatchBlockerCategory, classify_match_blocker
from ingestion.tecdoc.match_run_adapters import MatchEvaluation


@dataclass(frozen=True)
class MatchPatternObservation:
    """One aggregate-safe pattern observation; raw plate/VIN data never enters it."""

    pattern_key: str
    category: MatchBlockerCategory
    evidence: dict[str, Any]
    example: dict[str, Any]
    source_record_id: int = 0


def observe_match_pattern(
    raw: dict[str, Any], evaluation: MatchEvaluation, source_record_id: int = 0
) -> MatchPatternObservation | None:
    category = classify_match_blocker(evaluation)
    if category is None:
        return None
    candidates = list(evaluation.candidate_matches)
    references = sorted(
        {
            str(candidate.get("candidate_reference"))
            for candidate in candidates
            if candidate.get("candidate_reference")
        }
    )
    conflicts = sorted(
        {
            reason.removeprefix("conflict:")
            for reason in evaluation.reason_codes
            if reason.startswith("conflict:")
        }
        | {
            str(field)
            for candidate in candidates[:1]
            for field in candidate.get("evidence", {}).get("conflicting_fields", [])
        }
    )
    source_values: dict[str, Any] = {}
    if raw.get("brand"):
        source_values["manufacturer"] = str(raw["brand"]).strip()
    if raw.get("model"):
        source_values["model"] = str(raw["model"]).strip()
    if raw.get("body_code"):
        source_values["body_code"] = str(raw["body_code"]).strip().upper()
    if conflicts:
        source_values["conflicting_fields"] = conflicts
    if category.code in {
        "candidate_margin",
        "model_missing",
        "model_unmatched",
        "partial_or_phonetic_model",
        "model_source_conflict",
        "manufacturer_scope",
    }:
        source_values.setdefault("manufacturer", "unknown")
    if category.code in {
        "candidate_margin",
        "model_missing",
        "model_unmatched",
        "partial_or_phonetic_model",
        "model_source_conflict",
    }:
        source_values.setdefault("model", "missing")
    if category.code == "bodywork_conflict":
        source_values.setdefault("body_code", "missing")
    if category.code == "hard_technical_conflict":
        source_values.setdefault("conflicting_fields", ["technical evidence"])
    candidate_values: dict[str, Any] = {
        "candidate_count": len(candidates),
        "candidate_references": references,
    }
    if candidates:
        top_evidence = candidates[0].get("evidence") or {}
        if top_evidence.get("model"):
            candidate_values["catalog_model"] = str(top_evidence["model"])
        if top_evidence.get("matched_fields"):
            candidate_values["matched_fields"] = sorted(
                str(field) for field in top_evidence["matched_fields"]
            )
    evidence = {
        "category": category.code,
        "source_values": source_values,
        "candidate_values": candidate_values,
        "reason_codes": sorted(str(reason) for reason in evaluation.reason_codes),
    }
    encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    pattern_key = f"{category.code}:{hashlib.sha256(encoded.encode()).hexdigest()[:32]}"
    example = {
        "manufacturer": source_values.get("manufacturer", "Unknown"),
        "model": source_values.get("model", "Model unavailable"),
        "candidate_reference": references[0] if references else None,
    }
    return MatchPatternObservation(pattern_key, category, evidence, example, source_record_id)


def upsert_match_pattern_inventory(
    connection: Connection[Any],
    *,
    operation_id: UUID,
    batch_number: int,
    observations: list[MatchPatternObservation],
    source_record_id: int,
) -> None:
    """Upsert one batch of observations while retaining at most four examples."""

    grouped: dict[str, list[MatchPatternObservation]] = defaultdict(list)
    for observation in observations:
        grouped[observation.pattern_key].append(observation)
    if not grouped:
        observations_count = 0
    else:
        observations_count = len(observations)
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO {MATCH_RUN_PATTERN_BATCHES_TABLE}
                (operation_id, batch_number, last_source_record_id, observation_count)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (operation_id, batch_number) DO NOTHING
            """,
            (operation_id, batch_number, source_record_id, observations_count),
        )
        is_new_batch = cursor.rowcount == 1
        if not grouped:
            return
        for pattern_key, rows in grouped.items():
            examples: list[dict[str, Any]] = []
            seen: set[str] = set()
            for row in rows:
                encoded = json.dumps(row.example, sort_keys=True, separators=(",", ":"))
                if encoded not in seen:
                    seen.add(encoded)
                    examples.append(row.example)
                if len(examples) == 4:
                    break
            if is_new_batch:
                cursor.execute(
                    f"""
                    INSERT INTO {MATCH_RUN_PATTERN_INVENTORY_TABLE}
                        (operation_id, pattern_key, blocker_category, pattern_evidence,
                         occurrence_count, examples, first_source_record_id, last_source_record_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (operation_id, pattern_key) DO UPDATE SET
                        occurrence_count = {MATCH_RUN_PATTERN_INVENTORY_TABLE}.occurrence_count
                            + EXCLUDED.occurrence_count,
                        examples = (
                            SELECT jsonb_agg(value)
                            FROM (
                                SELECT DISTINCT value
                                FROM jsonb_array_elements(
                                    {MATCH_RUN_PATTERN_INVENTORY_TABLE}.examples || EXCLUDED.examples
                                )
                                LIMIT 4
                            ) merged
                        ),
                        last_source_record_id = EXCLUDED.last_source_record_id,
                        updated_at = now()
                    """,
                    (
                        operation_id,
                        pattern_key,
                        rows[0].category.code,
                        Jsonb(rows[0].evidence),
                        len(rows),
                        Jsonb(examples),
                        source_record_id,
                        source_record_id,
                    ),
                )
            cursor.executemany(
                f"INSERT INTO {MATCH_RUN_PATTERN_MEMBERS_TABLE} "
                "(operation_id, pattern_key, source_record_id) VALUES (%s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                [
                    (operation_id, pattern_key, row.source_record_id)
                    for row in rows
                    if row.source_record_id > 0
                ],
            )
