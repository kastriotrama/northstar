"""Look up how one vehicle matches the TecDoc KType catalog.

Search by plate against the locally imported Transportstyrelsen rows, or pass
the technical evidence directly to ask the catalog a hypothetical question.

The evaluation path mirrors `TecDocDryRunEvaluator` exactly -- policy routes,
manufacturer bridging, model recovery, the global-scope guard, then matching and
confidence routing -- so what this prints is what an audit would decide for the
same row under the current code. Normalization is recomputed live rather than
read from `core.normalization_results`, so results track the working tree.

Read-only: it writes nothing and persists no decision.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

import psycopg
from dotenv import load_dotenv
from neo4j import GraphDatabase
from psycopg.rows import dict_row

from ingestion.active_rules import load_active_rules
from ingestion.confidence_routing import ConfidenceRouter
from ingestion.fuzzy_matching import (
    FuzzyMatchConfig,
    FuzzyVehicleMatcher,
    ManufacturerCandidateIndex,
    VehicleMatchQuery,
)
from ingestion.normalization_rules import normalize_ts_record
from ingestion.tecdoc.manufacturer_mapping import TecDocManufacturerIndex
from ingestion.tecdoc.match_run_adapters import load_ktype_catalog

LOCAL_RAW_TABLE = "staging.transportstyrelsen_raw"
EVIDENCE_FIELDS = ("brand", "variant", "version", "model_no", "type_text", "eeg_type_approval")


def resolve_database_url(database_name: str | None) -> str:
    """Pick the database to read, preferring an explicit name over `.env`.

    `.env` may point `DATABASE_URL` at a database that does not hold the
    imported Transportstyrelsen rows, so `--database-name` builds a URL from the
    same credentials against a named database instead.
    """

    if database_name:
        user = os.environ["POSTGRES_USER"]
        password = os.environ["POSTGRES_PASSWORD"]
        port = os.environ.get("POSTGRES_HOST_PORT", "5432")
        host = os.environ.get("POSTGRES_HOST", "localhost")
        if host in {"postgres", "db"}:
            # Container-internal hostnames do not resolve from the host shell.
            host = "localhost"
        return f"postgresql://{user}:{password}@{host}:{port}/{database_name}"
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("Set DATABASE_URL in .env, or pass --database-name.")
    return url


def describe_connection(connection: psycopg.Connection) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        row = cursor.fetchone()
        database = row[0] if row else "?"
        cursor.execute(f"SELECT to_regclass('{LOCAL_RAW_TABLE}')")
        exists = cursor.fetchone()
        if not exists or exists[0] is None:
            return f"database {database}: {LOCAL_RAW_TABLE} is missing"
        cursor.execute(
            f"SELECT count(*), count(DISTINCT raw_record->>'plate') FROM {LOCAL_RAW_TABLE}"
        )
        counts = cursor.fetchone()
    rows, plates = (counts[0], counts[1]) if counts else (0, 0)
    return f"database {database}: {rows} rows, {plates} distinct plates"


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _integer(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) and int(value) > 0 else None


def _text(value: object) -> str | None:
    return str(value) if value else None


def fetch_raw_by_plate(
    connection: psycopg.Connection, plate: str
) -> dict[str, Any] | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            f"SELECT raw_record FROM {LOCAL_RAW_TABLE} "
            "WHERE upper(raw_record->>'plate') = upper(%s) "
            "ORDER BY ingested_at DESC, id DESC LIMIT 1",
            (plate.strip(),),
        )
        row = cursor.fetchone()
    return dict(row["raw_record"]) if row else None


def describe(
    raw: dict[str, Any] | None,
    *,
    manual: dict[str, Any] | None,
    rules: Any,
    manufacturer_rules: Any,
    index: ManufacturerCandidateIndex,
    matcher: FuzzyVehicleMatcher,
    router: ConfidenceRouter,
    bridge: TecDocManufacturerIndex,
    config: FuzzyMatchConfig,
    limit: int,
) -> dict[str, Any]:
    """Reproduce the audit decision for one vehicle, with candidate detail."""

    recovered_from: str | None = None
    if manual is not None:
        manufacturer: Any = manual.get("manufacturer")
        model: Any = manual.get("model")
        normalized: dict[str, Any] = dict(manual)
        source_evidence: dict[str, Any] = {}
        status = "manual"
    else:
        assert raw is not None
        outcome = normalize_ts_record(
            raw, rule_set=rules, manufacturer_entity_rules=manufacturer_rules
        )
        status = outcome.status
        if status == "failed":
            return {"terminal": "failed", "reasons": ["normalization_failed"]}
        normalized = _mapping(outcome.normalized)
        if status == "review_required":
            return {
                "terminal": "normalization_review",
                "reasons": list(outcome.review_reasons) or ["normalization_review_required"],
                "normalized": normalized,
            }
        route = normalized.get("record_route")
        if route in {"exclude_from_passenger_car_dataset", "quarantine_test_record"}:
            return {"terminal": "policy_excluded", "reasons": [f"policy:{route}"]}
        candidates_map = _mapping(outcome.candidates)
        manufacturer = normalized.get("manufacturer") or candidates_map.get("manufacturer")
        model = normalized.get("model_family") or candidates_map.get("model_family")
        source_evidence = {
            field: raw.get(field) for field in EVIDENCE_FIELDS if raw.get(field)
        }

    if not manufacturer:
        return {"terminal": "unmatched", "reasons": ["manufacturer_missing"]}

    if not model and source_evidence:
        evidence = {field: str(value) for field, value in source_evidence.items()}
        recovered = index.recover_model_from_evidence(str(manufacturer), evidence)
        if recovered is not None:
            model, recovered_from = recovered

    if not model:
        return {
            "terminal": "review_required",
            "reasons": ["model_evidence_missing"],
            "normalized": normalized,
        }

    energy = normalized.get("energy_sources")
    fuels = (
        frozenset(str(value) for value in energy)
        if isinstance(energy, list)
        else frozenset()
    )
    decision_scope = bridge.resolve(
        manufacturer=manufacturer, brand=source_evidence.get("brand")
    )
    scoped = (
        decision_scope.manufacturer
        if decision_scope.status == "resolved" and decision_scope.manufacturer
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
        return {"terminal": "review_required", "reasons": ["invalid_match_query_evidence"]}

    _, scope = index.lookup(
        query.manufacturer, similarity_threshold=config.manufacturer_scope_threshold
    )
    query_payload = {
        "manufacturer": query.manufacturer,
        "manufacturer_as_given": str(manufacturer),
        "model": query.model,
        "model_recovered_from": recovered_from,
        "year": query.year,
        "fuels": sorted(query.fuels),
        "engine_code": query.engine_code,
        "displacement_cc": query.displacement_cc,
        "power_kw": query.power_kw,
        "drive_type": query.drive_type,
        "bodywork": query.bodywork,
    }
    if scope == "global":
        return {
            "terminal": "review_required",
            "reasons": ["manufacturer_global_scope"],
            "normalization_status": status,
            "query": query_payload,
        }

    result = matcher.match(query)
    decision = router.route(result)
    terminal = "hard_conflict" if decision.hard_conflicts else decision.route
    top = result.candidates[0] if result.candidates else None
    runner_up = result.candidates[1] if len(result.candidates) > 1 else None

    return {
        "terminal": terminal,
        "normalization_status": status,
        "match_scope": result.scope,
        "match_reason": result.reason,
        "routing_confidence": decision.confidence,
        "routing_reasons": list(decision.reason_codes),
        "hard_conflicts": list(decision.hard_conflicts),
        "selected_candidate": decision.selected_candidate_reference,
        "separation_margin": (
            round(max(0.0, top.separation_score - runner_up.separation_score), 6)
            if top is not None and runner_up is not None
            else None
        ),
        "margin_gate": router.policy.minimum_candidate_margin,
        "query": query_payload,
        "candidates": [
            {
                "ktype": candidate.candidate_reference,
                "manufacturer": candidate.manufacturer,
                "model": candidate.model,
                "confidence": candidate.confidence,
                "separation_score": candidate.separation_score,
                "matched": list(candidate.matched_fields),
                "missing": list(candidate.missing_fields),
                "conflicting": list(candidate.conflicting_fields),
            }
            for candidate in result.candidates[:limit]
        ],
    }


def _render(label: str, report: dict[str, Any]) -> None:
    print(f"\n=== {label} ===")
    print(f"terminal:  {report.get('terminal')}")
    for key in ("normalization_status", "match_scope", "match_reason"):
        if report.get(key) is not None:
            print(f"{key}: {report[key]}")
    if report.get("reasons"):
        print(f"reasons:   {', '.join(report['reasons'])}")
    query = report.get("query")
    if query:
        print("\nvehicle evidence:")
        for key, value in query.items():
            if value not in (None, [], ""):
                print(f"  {key:24} {value}")
    if report.get("routing_reasons"):
        print(f"\nrouting:   confidence={report['routing_confidence']}")
        print(f"  reasons: {', '.join(report['routing_reasons'])}")
    if report.get("hard_conflicts"):
        print(f"  hard conflicts: {', '.join(report['hard_conflicts'])}")
    if report.get("separation_margin") is not None:
        print(
            f"  margin:  {report['separation_margin']} "
            f"(gate {report['margin_gate']})"
        )
    candidates = report.get("candidates") or []
    if candidates:
        print("\ncandidates:")
        for rank, candidate in enumerate(candidates, start=1):
            marker = "*" if candidate["ktype"] == report.get("selected_candidate") else " "
            print(
                f" {marker}{rank}. KType {candidate['ktype']}  "
                f"{candidate['manufacturer']} {candidate['model']}  "
                f"conf={candidate['confidence']}  sep={candidate['separation_score']}"
            )
            if candidate["matched"]:
                print(f"      matched:     {', '.join(candidate['matched'])}")
            if candidate["conflicting"]:
                print(f"      conflicting: {', '.join(candidate['conflicting'])}")
            if candidate["missing"]:
                print(f"      missing:     {', '.join(candidate['missing'])}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Look up how a vehicle matches the TecDoc KType catalog."
    )
    parser.add_argument("--plate", action="append", default=[], help="Repeatable.")
    parser.add_argument("--manufacturer")
    parser.add_argument("--model")
    parser.add_argument("--year", type=int)
    parser.add_argument("--fuel", action="append", default=[], help="Repeatable.")
    parser.add_argument("--engine-code")
    parser.add_argument("--displacement-cc", type=int)
    parser.add_argument("--power-kw", type=int)
    parser.add_argument("--drive-type")
    parser.add_argument("--bodywork")
    parser.add_argument("--limit", type=int, default=5, help="Candidates to show.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--database-name",
        default="northstar_passenger_496251_test",
        help=(
            "Database holding the imported TS rows. Pass an empty string to use "
            "DATABASE_URL from the environment instead."
        ),
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt for plates in a loop, loading the KType catalog only once.",
    )
    args = parser.parse_args()

    load_dotenv(override=False)

    manual: dict[str, Any] | None = None
    if args.manufacturer or args.model:
        if not (args.manufacturer and args.model):
            parser.error("--manufacturer and --model must be given together")
        manual = {
            "manufacturer": args.manufacturer,
            "model_family": args.model,
            "model": args.model,
            "production_year": args.year,
            "energy_sources": list(args.fuel),
            "engine_code": args.engine_code,
            "displacement_cc": args.displacement_cc,
            "power_kw": args.power_kw,
            "drive_type": args.drive_type,
            "bodywork_form": args.bodywork,
        }
    if not args.plate and manual is None and not args.interactive:
        parser.error("give --plate, --manufacturer with --model, or --interactive")

    with (
        psycopg.connect(resolve_database_url(args.database_name)) as local,
        GraphDatabase.driver(
            os.environ["NEO4J_URI"],
            auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
        ) as driver,
    ):
        rules, manufacturer_rules = load_active_rules(local)
        catalog = load_ktype_catalog(driver)
        config = FuzzyMatchConfig()
        index = ManufacturerCandidateIndex(catalog)
        matcher = FuzzyVehicleMatcher(index, config)
        router = ConfidenceRouter()
        bridge = TecDocManufacturerIndex(
            sorted({candidate.manufacturer for candidate in catalog}),
            manufacturer_rules or {},
        )

        reports: dict[str, Any] = {}
        common = {
            "rules": rules,
            "manufacturer_rules": manufacturer_rules,
            "index": index,
            "matcher": matcher,
            "router": router,
            "bridge": bridge,
            "config": config,
            "limit": args.limit,
        }

        if args.interactive:
            print(describe_connection(local))
            print("Enter a plate per line. Ctrl-D or blank line to quit.")
            while True:
                try:
                    entry = input("plate> ").strip()
                except EOFError:
                    print()
                    break
                if not entry:
                    break
                raw = fetch_raw_by_plate(local, entry)
                if raw is None:
                    print(f"  {entry}: not found in this database")
                    continue
                _render(entry, describe(raw, manual=None, **common))
            return
        for plate in args.plate:
            raw = fetch_raw_by_plate(local, plate)
            if raw is None:
                reports[plate] = {
                    "terminal": None,
                    "reasons": [
                        (
                            "plate not found — the local import is partial; "
                            f"{describe_connection(local)}"
                        )
                    ],
                }
                continue
            reports[plate] = describe(raw, manual=None, **common)
        if manual is not None:
            reports[f"{args.manufacturer} {args.model}"] = describe(
                None, manual=manual, **common
            )

    if args.json:
        print(json.dumps(reports, indent=2, sort_keys=True, default=str))
        return
    for label, report in reports.items():
        _render(label, report)


if __name__ == "__main__":
    main()
