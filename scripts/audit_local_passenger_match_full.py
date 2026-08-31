"""Run or resume the release-pinned full local TS-to-TecDoc blocker audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.conninfo import conninfo_to_dict

from ingestion.active_rules import load_active_rules
from ingestion.config import IngestionSettings
from ingestion.context_comparison import reviewed_context_policy
from ingestion.match_run_migrations import run_match_run_migrations
from ingestion.match_run_repository import MatchRunPins
from ingestion.tecdoc.match_run_adapters import (
    TecDocDryRunEvaluator,
    load_postgres_ktype_catalog,
)
from ingestion.tecdoc.model_aliases import ReviewedModelAliasIndex
from ingestion.tecdoc.remote_match_run import run_local_raw_dry_match_audit
from ingestion.tecdoc.source_model_rules import reviewed_source_model_policy


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--release-manifest", required=True, type=Path)
    parser.add_argument("--operation-id", required=True, type=UUID)
    parser.add_argument("--source-prefix", required=True)
    parser.add_argument("--expected-source-rows", required=True, type=int)
    parser.add_argument("--expected-candidates", required=True, type=int)
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--batch-size", type=int, default=25_000)
    parser.add_argument("--max-batches", type=int)
    args = parser.parse_args()

    release = json.loads(args.release_manifest.read_text())
    pins = dict(release["pins"])
    root = args.release_manifest.parents[2]
    context_payload = json.loads(
        (root / "ingestion/reviewed_context_policies/volvo_bodywork_reviewed_v1_20260830.json").read_text()
    )
    source_model_payload = json.loads(
        (root / "ingestion/reviewed_source_model_policies/peugeot_3008_hns_reviewed_v1_20260831.json").read_text()
    )
    context_policy = reviewed_context_policy(
        context_payload,
        expected_version=str(pins["context_policy_version"]),
        expected_digest=str(pins["context_policy_payload_sha256"]),
    )
    source_model_policy = reviewed_source_model_policy(
        source_model_payload,
        expected_version=str(pins["source_model_policy_version"]),
        expected_digest=str(pins["source_model_policy_payload_sha256"]),
    )
    if context_policy.content_digest != pins["context_policy_digest"]:
        raise ValueError("loaded context policy content differs from the release pin")
    if source_model_policy.content_digest != pins["source_model_policy_digest"]:
        raise ValueError("loaded source-model policy content differs from the release pin")
    settings = IngestionSettings(_env_file=args.env_file)  # type: ignore[call-arg]
    if conninfo_to_dict(settings.database_url).get("host") not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError("full local audit requires explicitly local PostgreSQL")

    with psycopg.connect(settings.database_url) as connection:
        run_match_run_migrations(connection)
        rules, manufacturers = load_active_rules(connection)
        if rules.version != pins["normalization_rule_version"]:
            raise ValueError("active normalization rules differ from the release manifest")
        catalog_version = str(pins["v6_catalog_version"])
        catalog = load_postgres_ktype_catalog(connection, batch_id=catalog_version)
        if len(catalog) != args.expected_candidates:
            raise ValueError("TecDoc candidate count differs from the release pin")
        evaluator = TecDocDryRunEvaluator(
            catalog,
            manufacturers,
            ReviewedModelAliasIndex(rules),
            context_policy=context_policy,
            source_model_policy=source_model_policy,
        )
        counts = run_local_raw_dry_match_audit(
            connection,
            pins=MatchRunPins(
                operation_id=args.operation_id,
                source_system="Transportstyrelsen",
                source_version=args.source_prefix,
                source_batch_prefix=args.source_prefix,
                expected_source_rows=args.expected_source_rows,
                normalization_rule_version=rules.version,
                candidate_catalog_version=f"postgres:{catalog_version}",
                policy_version=str(release["version"]),
                code_revision=args.code_revision,
                alignment_version="unpinned-legacy",
            ),
            rule_set=rules,
            manufacturer_rules=manufacturers,
            evaluator=evaluator,
            batch_size=args.batch_size,
            max_batches=args.max_batches,
        )
    print(json.dumps({"operation_id": str(args.operation_id), **counts.as_dict(), "processed": counts.processed}, sort_keys=True))


if __name__ == "__main__":
    main()
