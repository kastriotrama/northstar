# TecDoc vehicle ingestion runbook

This runbook is the operational contract for SCRUM-95–98. It deliberately
does not guess offsets in licensed fixed-width files. The provider table
dictionary must be applied during restore and exposed through the stable view
below before NorthStar reads any vehicle data.

## Source currently available

- Delivery: `REFERENCE_DATA_0326`
- Release: `0326`
- Format marker in `001.dat`: `2.70`
- License reference: must be recorded from the customer TecDoc agreement; do
  not copy the licensed source delivery into Git.

## 1. Restore and validate

1. Restore the provider dump/files into a dedicated PostgreSQL schema named
   `tecdoc_source`. Never restore into `public`, `core`, or `staging`.
2. Apply the provider's matching release `0326` table dictionary.
3. Create `tecdoc_source.northstar_vehicle_tree`. It must expose exactly the
   columns listed in `ingestion.tecdoc.extraction.VEHICLE_TREE_COLUMNS`, one
   row per KType, ordered/stably keyed by `ktype_id`.
4. Include the original provider table and row identifiers in
   `source_row_refs`. Shared engines, transmissions, and bodywork may appear
   on many KTypes; they must retain the same provider ID on every row.
5. Compare counts: provider passenger KTypes = view rows = distinct
   `ktype_id`. A duplicate KType or missing required key stops ingestion.

The adapter view owns provider-specific joins. It joins manufacturer, model,
KType/vehicle variant, engine, transmission, and bodywork tables and their
language-description tables. Optional facts are `NULL`; fabricated platform,
engine, transmission, or bodywork values are forbidden.

## 2. Configure and run

```bash
export TECDOC_SOURCE_PATH=/licensed/source/REFERENCE_DATA_0326
export TECDOC_SOURCE_VERSION=0326
export TECDOC_FORMAT_VERSION=2.70
export TECDOC_LICENSE_REFERENCE=<internal-license-reference>
export TECDOC_SOURCE_CHECKSUM=<sha256-of-source-manifest>
export TECDOC_SOURCE_SCHEMA=tecdoc_source
northstar-ingest tecdoc --batch-id tecdoc-0326-initial
```

The job records immutable batch metadata, stable source keys, canonical
candidates and opaque NorthStar IDs. Re-running the identical batch is safe:
the candidate and ledger writes are idempotent. Reusing a batch ID with a
different version, checksum, path, license reference, or count is rejected.

## 3. Reconciliation and evidence

- `core.tecdoc_source_batches` records source/version/checksum/count/status.
- `core.tecdoc_identity_registry` reuses the same opaque ID for a stable
  TecDoc entity key across later releases.
- `core.tecdoc_canonical_candidates` holds mapped candidates before graph
  loading. Multiple KTypes share one engine/transmission/bodywork candidate.
- `core.enrichment_ledger` records source version, batch, source key and raw
  row references for every candidate. It is append-only at database level.

Sample tracing starts with a KType alias candidate, follows its
`target_source_key` to the vehicle variant, and uses each candidate's
`source_row_refs` to locate the exact restored provider rows.
