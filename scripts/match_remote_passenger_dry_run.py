"""Run the resumable disk-bounded remote TS-to-TecDoc audit."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from uuid import UUID

import psycopg
from neo4j import GraphDatabase

from ingestion.active_rules import load_active_rules
from ingestion.match_run_migrations import run_match_run_migrations
from ingestion.match_run_repository import MatchRunPins
from ingestion.tecdoc.match_run_adapters import TecDocDryRunEvaluator, load_ktype_catalog
from ingestion.tecdoc.remote_match_run import run_remote_dry_match_audit


def _required_url(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} must be set")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operation-id", type=UUID, required=True)
    parser.add_argument("--source-version", required=True)
    parser.add_argument("--expected-source-rows", type=int, required=True)
    parser.add_argument("--normalization-rule-version", required=True)
    parser.add_argument("--candidate-catalog-version", required=True)
    parser.add_argument("--expected-ktype-count", type=int, required=True)
    parser.add_argument("--policy-version", required=True)
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--batch-size", type=int, default=25_000)
    parser.add_argument("--max-batches", type=int, default=None)
    args = parser.parse_args()

    with (
        psycopg.connect(_required_url("DATABASE_URL")) as local,
        psycopg.connect(_required_url("REMOTE_DATABASE_URL")) as remote,
        GraphDatabase.driver(
            _required_url("NEO4J_URI"),
            auth=(_required_url("NEO4J_USER"), _required_url("NEO4J_PASSWORD")),
        ) as driver,
    ):
        run_match_run_migrations(local)
        rules, manufacturer_rules = load_active_rules(local)
        catalog = load_ktype_catalog(driver)
        if len(catalog) != args.expected_ktype_count:
            raise ValueError(
                f"TecDoc KType count mismatch: expected {args.expected_ktype_count}, "
                f"found {len(catalog)}"
            )
        pins = MatchRunPins(
            operation_id=args.operation_id,
            source_system="Transportstyrelsen",
            source_version=args.source_version,
            source_batch_prefix="remote:public.swedish_vehicles",
            expected_source_rows=args.expected_source_rows,
            normalization_rule_version=args.normalization_rule_version,
            candidate_catalog_version=args.candidate_catalog_version,
            policy_version=args.policy_version,
            code_revision=args.code_revision,
        )
        counts = run_remote_dry_match_audit(
            local,
            remote,
            pins=pins,
            rule_set=rules,
            manufacturer_rules=manufacturer_rules,
            evaluator=TecDocDryRunEvaluator(catalog, manufacturer_rules),
            batch_size=args.batch_size,
            max_batches=args.max_batches,
        )
    print(json.dumps(asdict(counts), sort_keys=True))


if __name__ == "__main__":
    main()
