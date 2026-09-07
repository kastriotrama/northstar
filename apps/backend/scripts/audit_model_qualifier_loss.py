"""Reproduce model-qualifier loss against a pinned v6 matcher report.

The audit is read-only and plate-free.  It measures where a reviewed base-family
normalization discards trailing registry model text, then asks whether the pinned
TecDoc catalog contains a more specific family explicitly named by that text.
Observations are never emitted as active rules.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict

from ingestion.active_rules import load_active_rules
from ingestion.config import IngestionSettings
from ingestion.normalization_rules import normalize_ts_record
from ingestion.tecdoc.manufacturer_mapping import TecDocManufacturerIndex
from ingestion.tecdoc.match_run_adapters import (
    load_postgres_ktype_catalog,
    tecdoc_model_aliases,
)
from ingestion.tecdoc.remote_match_run import _fetch_local_raw_page
from scripts.validate_local_matcher_cohort import digest, write_private_json

_NON_ALPHANUMERIC = re.compile(r"[^A-Z0-9]+")
_PLUS_DIGIT = re.compile(r"\+\s*\d")


def model_tokens(value: object) -> tuple[str, ...]:
    """Return accent- and punctuation-tolerant comparison tokens."""

    normalized = unicodedata.normalize("NFKD", str(value or "").upper())
    without_marks = "".join(char for char in normalized if not unicodedata.combining(char))
    return tuple(_NON_ALPHANUMERIC.sub(" ", without_marks).split())


def loses_trailing_qualifier(raw_model: object, normalized_family: object) -> bool:
    """Require a strict whole-token prefix; unrelated canonical rewrites do not count."""

    raw_tokens = model_tokens(raw_model)
    family_tokens = model_tokens(normalized_family)
    return bool(
        family_tokens
        and len(raw_tokens) > len(family_tokens)
        and raw_tokens[: len(family_tokens)] == family_tokens
    )


def specific_catalog_labels(
    raw_model: object,
    normalized_family: object,
    labels: tuple[str, ...],
) -> tuple[str, ...]:
    """Return longest catalog labels wholly and contiguously named by the source."""

    raw_tokens = model_tokens(raw_model)
    family_tokens = model_tokens(normalized_family)
    matches: dict[tuple[str, ...], str] = {}
    for label in labels:
        label_tokens = model_tokens(label)
        if len(label_tokens) <= len(family_tokens) or not label_tokens:
            continue
        width = len(label_tokens)
        if any(raw_tokens[start : start + width] == label_tokens for start in range(len(raw_tokens) - width + 1)):
            matches.setdefault(label_tokens, label)
    if not matches:
        return ()
    longest = max(len(tokens) for tokens in matches)
    return tuple(sorted(label for tokens, label in matches.items() if len(tokens) == longest))


def observation_risks(
    *, manufacturer: str, raw_model: str, normalized_family: str, catalog_label: str
) -> tuple[str, ...]:
    """Flag cohorts that the remote measurement already shows are not rule-ready."""

    risks = {"domain_review_required"}
    family_tokens = model_tokens(normalized_family)
    label_tokens = model_tokens(catalog_label)
    if _PLUS_DIGIT.search(raw_model):
        risks.add("plus_digit_semantics_ambiguous")
    if manufacturer.upper() in {"VW", "VOLKSWAGEN"} and family_tokens == ("GOLF",):
        risks.add("shared_golf_base_family")
    suffix = label_tokens[len(family_tokens) :]
    if len(suffix) == 1 and len(suffix[0]) == 1 and suffix[0].isalpha():
        risks.add("generation_letter_ambiguous")
    if len(label_tokens) >= len(family_tokens) * 2 and (
        label_tokens[: len(family_tokens)] == family_tokens
        and label_tokens[len(family_tokens) : len(family_tokens) * 2] == family_tokens
    ):
        risks.add("duplicated_catalog_label")
    if manufacturer.upper() == "VOLVO" and label_tokens[-2:] == ("CROSS", "COUNTRY"):
        risks.add("reconcile_existing_volvo_policy")
    return tuple(sorted(risks))


def _catalog_labels_by_manufacturer(catalog: tuple[Any, ...]) -> dict[str, tuple[str, ...]]:
    labels: dict[str, set[str]] = defaultdict(set)
    for candidate in catalog:
        # Family aliases only: candidate.model_aliases also contains source trim
        # names, which are not safe evidence of a distinct model family.
        family_labels = tecdoc_model_aliases(candidate.model) or (candidate.model,)
        labels[candidate.manufacturer].update(family_labels)
    return {manufacturer: tuple(sorted(values)) for manufacturer, values in labels.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    report = json.loads(args.report.read_text())
    settings = IngestionSettings(_env_file=args.env_file)  # type: ignore[call-arg]
    if conninfo_to_dict(settings.database_url).get("host") not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("qualifier audit requires local PostgreSQL")
    with psycopg.connect(settings.database_url, options="-c default_transaction_read_only=on") as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        rules, manufacturers = load_active_rules(connection)
        catalog = load_postgres_ktype_catalog(connection, batch_id=str(report["catalog_version"]))
        raw_rows = _fetch_local_raw_page(
            connection,
            source_batch_prefix=str(report["source_prefix"]),
            after_id=0,
            limit=int(report["count"]),
        )
    if digest(raw_rows) != report["source_digest"]:
        raise ValueError("source rows differ from the pinned matcher report")
    if digest([asdict(candidate) for candidate in catalog]) != report["catalog_digest"]:
        raise ValueError("catalog differs from the pinned matcher report")
    if digest([asdict(rules), manufacturers]) != report["rules_digest"]:
        raise ValueError("normalization rules differ from the pinned matcher report")

    report_rows = {str(row["row_key"]): row for row in report["records"]}
    catalog_labels = _catalog_labels_by_manufacturer(catalog)
    manufacturer_bridge = TecDocManufacturerIndex(tuple(catalog_labels), manufacturers)
    counts: Counter[str] = Counter(source_rows=len(raw_rows))
    observations: dict[tuple[str, str, str, tuple[str, ...]], Counter[str]] = defaultdict(Counter)
    observation_reasons: dict[
        tuple[str, str, str, tuple[str, ...]], Counter[str]
    ] = defaultdict(Counter)
    unresolved_reasons: Counter[str] = Counter()
    for source_id, raw in raw_rows:
        raw_model = str(raw.get("model") or "").strip()
        if not raw_model:
            continue
        counts["registry_model_rows"] += 1
        outcome = normalize_ts_record(
            raw, rule_set=rules, manufacturer_entity_rules=manufacturers
        )
        normalized_family = str(outcome.normalized.get("model_family") or "").strip()
        if not loses_trailing_qualifier(raw_model, normalized_family):
            continue
        counts["qualifier_dropped"] += 1
        source_manufacturer = str(outcome.normalized.get("manufacturer") or "")
        scope = manufacturer_bridge.resolve(
            manufacturer=source_manufacturer,
            brand=raw.get("brand"),
        )
        if scope.status != "resolved" or not scope.manufacturer:
            counts["manufacturer_scope_unresolved"] += 1
            continue
        labels = specific_catalog_labels(
            raw_model,
            normalized_family,
            catalog_labels.get(scope.manufacturer, ()),
        )
        if not labels:
            counts["specific_catalog_family_missing"] += 1
            continue
        if len(labels) > 1:
            counts["specific_catalog_family_ambiguous"] += 1
            continue
        counts["specific_catalog_family_unique"] += 1
        label = labels[0]
        risks = observation_risks(
            manufacturer=scope.manufacturer,
            raw_model=raw_model,
            normalized_family=normalized_family,
            catalog_label=label,
        )
        row_key = digest([source_id, raw])
        report_row = report_rows[row_key]
        terminal = str(report_row["terminal"])
        observation_key = (scope.manufacturer, normalized_family, label, risks)
        observations[observation_key][terminal] += 1
        for reason in report_row["reason_codes"]:
            observation_reasons[observation_key][str(reason)] += 1
            if terminal in {"review_required", "hard_conflict"}:
                unresolved_reasons[str(reason)] += 1

    payload = {
        "status": "observations_only",
        "activation_ready": False,
        "counts": dict(sorted(counts.items())),
        "observations": [
            {
                "manufacturer": manufacturer,
                "normalized_family": normalized_family,
                "catalog_label": label,
                "support": sum(terminals.values()),
                "terminal_counts": dict(sorted(terminals.items())),
                "reason_counts": dict(sorted(observation_reasons[(manufacturer, normalized_family, label, risks)].items())),
                "risk_flags": list(risks),
            }
            for (manufacturer, normalized_family, label, risks), terminals in sorted(
                observations.items(), key=lambda item: (-sum(item[1].values()), item[0])
            )
        ],
        "unresolved_reason_counts": dict(
            sorted(unresolved_reasons.items(), key=lambda item: (-item[1], item[0]))
        ),
        "source_report": args.report.name,
        "source_digest": report["source_digest"],
        "catalog_version": report["catalog_version"],
        "catalog_digest": report["catalog_digest"],
        "rule_version": rules.version,
        "rules_digest": report["rules_digest"],
        "read_only": True,
        "postgres_writes": 0,
        "neo4j_writes": 0,
        "contains_private_plates": False,
        "contains_private_vins": False,
    }
    write_private_json(args.output, payload)
    print(json.dumps({
        "counts": payload["counts"],
        "observation_count": len(payload["observations"]),
        "activation_ready": False,
        "postgres_writes": 0,
        "neo4j_writes": 0,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
