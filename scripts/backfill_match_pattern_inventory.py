"""Backfill exhaustive plate-free pattern inventory for processed local audit rows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.conninfo import conninfo_to_dict

from ingestion.config import IngestionSettings
from ingestion.active_rules import load_active_rules
from ingestion.match_pattern_inventory import observe_match_pattern, upsert_match_pattern_inventory
from ingestion.match_run_migrations import MATCH_RUNS_TABLE, run_match_run_migrations
from ingestion.tecdoc.match_run_adapters import TecDocDryRunEvaluator, load_postgres_ktype_catalog
from ingestion.tecdoc.remote_match_run import _evaluate_raw_record, _fetch_local_raw_page
from ingestion.context_comparison import reviewed_context_policy
from ingestion.tecdoc.model_aliases import ReviewedModelAliasIndex
from ingestion.tecdoc.source_model_rules import reviewed_source_model_policy


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--release-manifest", required=True, type=Path)
    parser.add_argument("--operation-id", required=True, type=UUID)
    parser.add_argument("--source-prefix", required=True)
    parser.add_argument("--batch-size", type=int, default=25_000)
    parser.add_argument("--max-source-record-id", type=int)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 100_000:
        raise ValueError("batch size must be between 1 and 100000")

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
    settings = IngestionSettings(_env_file=args.env_file)  # type: ignore[call-arg]
    if conninfo_to_dict(settings.database_url).get("host") not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("pattern backfill requires explicitly local PostgreSQL")

    with psycopg.connect(settings.database_url) as connection:
        run_match_run_migrations(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT last_source_record_id FROM {MATCH_RUNS_TABLE} WHERE operation_id=%s",
                (args.operation_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise ValueError("operation does not exist")
        target_id = args.max_source_record_id or int(row[0])
        rules, manufacturers = load_active_rules(connection)
        catalog = load_postgres_ktype_catalog(
            connection, batch_id=str(pins["v6_catalog_version"])
        )
        evaluator = TecDocDryRunEvaluator(
            catalog,
            manufacturers,
            ReviewedModelAliasIndex(rules),
            context_policy=context_policy,
            source_model_policy=source_model_policy,
        )
        after_id = 0
        batch_number = 0
        processed = 0
        while True:
            records = tuple(
                record
                for record in _fetch_local_raw_page(
                    connection,
                    source_batch_prefix=args.source_prefix,
                    after_id=after_id,
                    limit=args.batch_size,
                )
                if record[0] <= target_id
            )
            if not records:
                break
            observations = []
            for source_id, raw in records:
                evaluation = _evaluate_raw_record(
                    raw,
                    source_record_id=source_id,
                    rule_set=rules,
                    manufacturer_rules=manufacturers,
                    evaluator=evaluator,
                )
                if observation := observe_match_pattern(raw, evaluation):
                    observations.append(observation)
            batch_number += 1
            processed += len(records)
            upsert_match_pattern_inventory(
                connection,
                operation_id=args.operation_id,
                batch_number=batch_number,
                observations=observations,
                source_record_id=records[-1][0],
            )
            connection.commit()
            after_id = records[-1][0]
        print(json.dumps({
            "operation_id": str(args.operation_id),
            "target_source_record_id": target_id,
            "processed": processed,
            "pattern_batches": batch_number,
        }, sort_keys=True))


if __name__ == "__main__":
    main()
