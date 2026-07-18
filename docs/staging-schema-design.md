# Staging Schema Design — PostgreSQL Landing Zone

| Field | Value |
|---|---|
| Version | `0.1` |
| Status | Accepted staging schema contract |
| Owner | NorthStar backend team |
| Jira story | SCRUM-16 (Story 3.1) |
| Scope | `staging` schema, TecDoc staging table pattern, `transportstyrelsen_raw`, COPY load contract |
| Last reviewed | 2026-07-16 |

## 1. Purpose and principles

PostgreSQL staging is the **untouched landing zone** for raw source dumps
(Phase 1 plan, Epic 3). Two rules, both load-bearing:

1. **No transformation at load time.** Every source record lands exactly as
   extracted, as a JSONB payload. Normalization (Epic 4) reads staging;
   staging never reads or writes the graph.
2. **Reprocessable.** Raw rows are never mutated or deleted by
   normalization. Re-running normalization against staging must be safe to
   repeat — this schema exists so a dictionary fix or bug fix can be
   replayed from the original data without re-fetching the source.

Staging tables deliberately do **not** use opaque `northstar` node IDs —
they predate identity resolution. Rows are keyed by a plain `BIGSERIAL`,
which is an implementation detail, not a canonical identifier.

## 2. The `staging` schema

Created once by the `create_staging_schema` migration statement. Every
table in this document lives under it.

**Ownership and permission assumptions (Phase 1):** the migration runs as
the single application role from `DATABASE_URL` (locally the `app` user
from compose), which therefore owns the schema and every table in it. There
are no separate reader/writer/migrator roles yet — the ingestion service is
the only writer and the only reader of staging. Revisit this split when the
staging cloud environment lands (SCRUM-8) or when a second consumer of
staging data appears; until then, do not grant staging access to additional
roles.

## 3. Shared landing-zone table shape

Every staging table --- both `transportstyrelsen_raw` and every
`tecdoc_<entity>` table --- has the identical shape:

| Column | Type | Nullable | Meaning |
|---|---|---|---|
| `id` | `BIGSERIAL` | no (PK) | Row identity, staging-local only; never referenced outside this schema |
| `source_batch_id` | `TEXT` | no | Identifies the load run; see `ingest_job_runs` (Story 3.4, not yet built) for batch bookkeeping |
| `ingested_at` | `TIMESTAMPTZ` | no, defaults to `now()` | Server-assigned load timestamp |
| `raw_record` | `JSONB` | no | The source record, verbatim, as delivered by the extractor |

One shared shape means one shared loader (§6) and one shared review
checklist item (§8) instead of per-table special cases.

**Source references live inside `raw_record` — deliberately.** The
source's own identifiers (a TecDoc record number, a Transportstyrelsen
vehicle key) are preserved verbatim inside the JSONB payload, not extracted
into dedicated columns. Staging is schema-on-read: normalization (Epic 4)
extracts and validates source keys when it consumes these rows, so a source
adding or renaming an identifier field never requires a staging migration.

## 4. TecDoc staging table pattern

TecDoc ships as multiple related tables (manufacturer, model, k-type,
engine, body enums --- Phase 1 plan Epic 5). The real TecDoc dump schema is
extracted in Epic 5, not this story; Story 3.1 defines the **pattern** every
`staging.tecdoc_<entity>` table follows so Epic 5 only has to name entities,
never hand-write DDL:

```python
from ingestion.staging_migrations import tecdoc_staging_table_statement

model_statement = tecdoc_staging_table_statement("model")
```

This produces `staging.tecdoc_model` with the shared shape from §3. Entity
names must be lowercase `snake_case` (validated; anything else raises
`ValueError` rather than reaching SQL).

**Worked example, included in this story:** `staging.tecdoc_manufacturer`
(statement name `create_staging_tecdoc_manufacturer_table`), proving the
pattern end-to-end. Epic 5 adds `tecdoc_model`, `tecdoc_engine`, and the
rest the same way.

## 5. `staging.transportstyrelsen_raw`

Uses the shared shape from §3 directly (statement name
`create_staging_transportstyrelsen_raw_table`). One row per raw
registration record.

## 6. COPY load contract

Loading is bulk `COPY`, not row-by-row `INSERT` (Phase 1 plan: "loaded via
`COPY` for speed"). `ingestion.staging_loaders.copy_raw_records` is the only
sanctioned entry point:

```python
from ingestion.staging_loaders import copy_raw_records

row_count = copy_raw_records(
    connection,
    table="staging.transportstyrelsen_raw",
    source_batch_id="2026-07-16-full-export",
    expected_source_count=source_record_count,
    records=extracted_records,  # Iterable[dict], one dict per source row
)
```

Rules:

- `table` must be one of the tables created in §3/§4/§5 --- the loader
  validates against an allow-list derived from the migration statements
  and raises `ValueError` for anything else. This is a deliberate guard
  against building `COPY` targets from untrusted or mistyped strings.
- Each `record` becomes one row: `source_batch_id` (as given),
  `ingested_at` (server-assigned), `raw_record` (the dict, JSON-encoded
  verbatim --- no field renaming, no type coercion).
- The loader performs **no deduplication**. Loading the same batch twice
  produces duplicate rows. Resumable, no-op-on-rerun batch tracking is
  Story 3.4's `ingest_job_runs` table (not yet built); until then, callers
  are responsible for choosing a `source_batch_id` and not re-running a
  completed batch.

**Row-count validation is required and atomic.** Every caller passes the
record count extracted from the source as `expected_source_count`.
`copy_raw_records` verifies that this equals both the number written through
COPY and the landed count for `source_batch_id`. It commits only when all
three agree; any mismatch raises `BatchRowCountMismatchError` and rolls the
transaction back:

```python
from ingestion.staging_loaders import copy_raw_records

written = copy_raw_records(
    connection,
    table="staging.transportstyrelsen_raw",
    source_batch_id=batch_id,
    expected_source_count=source_record_count,
    records=extracted_records,
)
assert written == source_record_count
```

A mismatch means the load failed and no rows from that transaction remain.
The separate `count_batch_rows` helper remains available for monitoring and
post-load investigation.
The same three-way check applies to every `tecdoc_<entity>` table — this is
the "row counts match source" acceptance rule from the Phase 1 plan,
Stories 5.1 and 6.1.

## 7. Migration runner

```sh
northstar-ingest migrate-staging
```

Every statement uses `IF NOT EXISTS`, so the migration is idempotent ---
running it twice succeeds and the second run is a no-op. Before committing,
the runner verifies every registered staging table's column order, data types,
nullability, defaults, and `id` primary key. An incompatible existing table
raises `StagingSchemaContractError` instead of being silently accepted.
Statement names below are a stable contract asserted by the doc contract tests.

| Name | Kind | Object |
|---|---|---|
| `create_staging_schema` | schema | `staging` |
| `create_staging_tecdoc_manufacturer_table` | table | `staging.tecdoc_manufacturer` |
| `create_staging_transportstyrelsen_raw_table` | table | `staging.transportstyrelsen_raw` |

## 8. Schema PR review checklist

- [ ] New TecDoc entities are added via `tecdoc_staging_table_statement`,
      never hand-written DDL.
- [ ] No staging table gains a column outside the shared shape (§3) without
      updating this document and its contract tests.
- [ ] `raw_record` stays untransformed --- no normalization logic in
      `ingestion/staging_migrations.py` or `ingestion/staging_loaders.py`.
- [ ] New `COPY` targets are added to a migration statement first, so they
      appear in `ALLOWED_STAGING_TABLES` automatically.
- [ ] Migration statements keep `IF NOT EXISTS` and stable names.
- [ ] Every new load path performs the three-way row-count validation
      (source count == `copy_raw_records` return == `count_batch_rows`).
