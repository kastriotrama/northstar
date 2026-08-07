"""Versioned golden-corpus verification for normalization and reconciliation."""

from __future__ import annotations

import argparse
import difflib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from ingestion.confidence_routing import ConfidenceRouter
from ingestion.fuzzy_matching import (
    FuzzyVehicleMatcher,
    ManufacturerCandidateIndex,
    VehicleCandidate,
    VehicleMatchQuery,
)
from ingestion.normalization_rules import normalize_ts_record

GOLDEN_CORPUS_VERSION = "normalization-reconciliation-golden-v1"
MINIMUM_GOLDEN_CASES = 200
DEFAULT_CORPUS_PATH = Path("tests/golden/normalization-reconciliation-v1.json")
SENSITIVE_FIELD_NAMES = frozenset(
    {
        "plate",
        "registration_number",
        "vin",
        "vehicle_identification_number",
        "personal_identity_number",
        "personnummer",
        "email",
        "phone",
        "address",
    }
)


class GoldenCorpusError(ValueError):
    """Raised when the corpus contract or an approved result is violated."""


@dataclass(frozen=True)
class GoldenCorpusReport:
    corpus_version: str
    case_count: int
    normalization_count: int
    reconciliation_count: int


def _required_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GoldenCorpusError(f"{label} must be an object")
    return cast(Mapping[str, Any], value)


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GoldenCorpusError(f"{label} must be a non-empty string")
    return value


def _optional_int(value: object, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise GoldenCorpusError(f"{label} must be an integer or null")
    return value


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise GoldenCorpusError(f"{label} must be an array of strings")
    return tuple(value)


def _assert_sanitized(value: object, path: str = "corpus") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            if key_text.casefold() in SENSITIVE_FIELD_NAMES:
                raise GoldenCorpusError(f"sensitive field {key_text!r} is not allowed at {path}")
            _assert_sanitized(nested, f"{path}.{key_text}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_sanitized(nested, f"{path}[{index}]")


def _normalization_snapshot(case_input: Mapping[str, Any]) -> dict[str, Any]:
    raw_record = _required_mapping(case_input.get("raw_record"), "input.raw_record")
    outcome = normalize_ts_record(dict(raw_record))
    return {
        "kind": "normalization",
        "status": outcome.status,
        "normalized": outcome.normalized,
        "candidates": outcome.candidates,
        "applied_rule_ids": list(outcome.applied_rule_ids),
        "candidate_rule_ids": list(outcome.candidate_rule_ids),
        "review_reasons": list(outcome.review_reasons),
        "confidence": outcome.confidence,
        "pipeline_version": outcome.pipeline_version,
        "decision_trace": [entry.to_payload() for entry in outcome.decision_trace],
        "rule_matches": [match.to_payload() for match in outcome.rule_matches],
    }


def _candidate_from_payload(value: object, index: int) -> VehicleCandidate:
    payload = _required_mapping(value, f"input.catalog[{index}]")
    return VehicleCandidate(
        candidate_reference=_required_string(
            payload.get("candidate_reference"), f"input.catalog[{index}].candidate_reference"
        ),
        candidate_type=_required_string(
            payload.get("candidate_type", "TecDocKType"),
            f"input.catalog[{index}].candidate_type",
        ),
        manufacturer=_required_string(
            payload.get("manufacturer"), f"input.catalog[{index}].manufacturer"
        ),
        model=_required_string(payload.get("model"), f"input.catalog[{index}].model"),
        model_aliases=_string_tuple(
            payload.get("model_aliases"), f"input.catalog[{index}].model_aliases"
        ),
        manufacturer_aliases=_string_tuple(
            payload.get("manufacturer_aliases"),
            f"input.catalog[{index}].manufacturer_aliases",
        ),
        year_from=_optional_int(payload.get("year_from"), f"input.catalog[{index}].year_from"),
        year_to=_optional_int(payload.get("year_to"), f"input.catalog[{index}].year_to"),
        fuels=frozenset(_string_tuple(payload.get("fuels"), f"input.catalog[{index}].fuels")),
        engine_codes=frozenset(
            _string_tuple(payload.get("engine_codes"), f"input.catalog[{index}].engine_codes")
        ),
    )


def _reconciliation_snapshot(case_input: Mapping[str, Any]) -> dict[str, Any]:
    catalog_payload = case_input.get("catalog")
    if not isinstance(catalog_payload, list) or not catalog_payload:
        raise GoldenCorpusError("input.catalog must be a non-empty array")
    catalog = tuple(
        _candidate_from_payload(candidate, index) for index, candidate in enumerate(catalog_payload)
    )
    query_payload = _required_mapping(case_input.get("query"), "input.query")
    query = VehicleMatchQuery(
        model=_required_string(query_payload.get("model"), "input.query.model"),
        manufacturer=(
            _required_string(query_payload["manufacturer"], "input.query.manufacturer")
            if query_payload.get("manufacturer") is not None
            else None
        ),
        year=_optional_int(query_payload.get("year"), "input.query.year"),
        fuels=frozenset(_string_tuple(query_payload.get("fuels"), "input.query.fuels")),
        engine_code=(
            _required_string(query_payload["engine_code"], "input.query.engine_code")
            if query_payload.get("engine_code") is not None
            else None
        ),
    )
    match = FuzzyVehicleMatcher(ManufacturerCandidateIndex(catalog)).match(query)
    decision = ConfidenceRouter().route(match)
    return {
        "kind": "reconciliation",
        "match": {
            "scope": match.scope,
            "eligible_for_auto_resolution": match.eligible_for_auto_resolution,
            "reason": match.reason,
        },
        "routing": decision.to_payload(),
    }


def evaluate_case(case: Mapping[str, Any]) -> dict[str, Any]:
    """Run one corpus case through production normalization/matching rules."""

    case_input = _required_mapping(case.get("input"), "case.input")
    kind = _required_string(case_input.get("kind"), "case.input.kind")
    if kind == "normalization":
        return _normalization_snapshot(case_input)
    if kind == "reconciliation":
        return _reconciliation_snapshot(case_input)
    raise GoldenCorpusError(f"unsupported case kind: {kind!r}")


def _load_document(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GoldenCorpusError(f"cannot load golden corpus {path}: {error}") from error
    return dict(_required_mapping(value, "corpus"))


def _validated_cases(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    version = _required_string(document.get("corpus_version"), "corpus_version")
    if version != GOLDEN_CORPUS_VERSION:
        raise GoldenCorpusError(
            f"unsupported corpus version {version!r}; expected {GOLDEN_CORPUS_VERSION!r}"
        )
    raw_cases = document.get("cases")
    if not isinstance(raw_cases, list):
        raise GoldenCorpusError("cases must be an array")
    if len(raw_cases) < MINIMUM_GOLDEN_CASES:
        raise GoldenCorpusError(
            f"corpus has {len(raw_cases)} cases; at least {MINIMUM_GOLDEN_CASES} are required"
        )
    cases = [_required_mapping(case, f"cases[{index}]") for index, case in enumerate(raw_cases)]
    case_ids = [_required_string(case.get("id"), "case.id") for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise GoldenCorpusError("case IDs must be unique")
    for case in cases:
        _required_string(case.get("description"), f"case {case['id']} description")
        tags = _string_tuple(case.get("tags"), f"case {case['id']} tags")
        if not tags:
            raise GoldenCorpusError(f"case {case['id']} must have at least one tag")
        _required_mapping(case.get("expected"), f"case {case['id']} expected")
    _assert_sanitized(document)
    return cases


def _render_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def verify_corpus(path: Path = DEFAULT_CORPUS_PATH) -> GoldenCorpusReport:
    """Verify every approved result and raise with a readable per-case diff."""

    document = _load_document(path)
    cases = _validated_cases(document)
    counts = {"normalization": 0, "reconciliation": 0}
    regressions: list[str] = []
    for case in cases:
        expected = _required_mapping(case.get("expected"), f"case {case['id']} expected")
        actual = evaluate_case(case)
        kind = cast(str, actual["kind"])
        counts[kind] += 1
        if actual != expected:
            expected_text = _render_json(expected).splitlines(keepends=True)
            actual_text = _render_json(actual).splitlines(keepends=True)
            difference = "".join(
                difflib.unified_diff(
                    expected_text,
                    actual_text,
                    fromfile=f"approved/{case['id']}",
                    tofile=f"actual/{case['id']}",
                )
            )
            regressions.append(
                f"unapproved golden regression in {case['id']}: {case['description']}\n{difference}"
            )
    if regressions:
        raise GoldenCorpusError(
            f"{len(regressions)} unapproved golden regression(s)\n\n" + "\n".join(regressions)
        )
    return GoldenCorpusReport(
        corpus_version=cast(str, document["corpus_version"]),
        case_count=len(cases),
        normalization_count=counts["normalization"],
        reconciliation_count=counts["reconciliation"],
    )


def approve_corpus(path: Path = DEFAULT_CORPUS_PATH) -> GoldenCorpusReport:
    """Explicitly replace expected results, for reviewable golden-file updates."""

    document = _load_document(path)
    cases = _validated_cases(document)
    for case in cases:
        if not isinstance(case, dict):
            raise GoldenCorpusError("approval requires mutable JSON case objects")
        case["expected"] = evaluate_case(case)
    path.write_text(_render_json(document), encoding="utf-8")
    return verify_corpus(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify or explicitly approve the golden corpus.")
    parser.add_argument("action", choices=("verify", "approve"))
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_CORPUS_PATH)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = approve_corpus(args.path) if args.action == "approve" else verify_corpus(args.path)
    print(
        f"{report.corpus_version}: {report.case_count} cases passed "
        f"({report.normalization_count} normalization, "
        f"{report.reconciliation_count} reconciliation)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
