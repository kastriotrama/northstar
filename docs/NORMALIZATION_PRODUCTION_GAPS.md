# Normalization production gaps

| Field | Value |
|---|---|
| Status | Working handoff — remaining work to run normalization unattended in production |
| Scope | Transportstyrelsen normalization only; graph loading and resolve API are out of scope |
| Related | [Production deployment](PRODUCTION_DEPLOYMENT.md), [normalization command](normalization-command.md), [review dashboard](normalization-review-dashboard.md) |
| Last reviewed | 2026-08-14 |

## 1. Already complete

Verified against the code on this branch:

- Normalization pipeline (`ingestion/normalization_pipeline.py`) and reviewed
  database-rule structure (`ingestion/active_rules.py`,
  `ingestion/rule_delta.py`).
- Latest reviewed database version: `ts-review-20260813T104653142376Z`.
- Reproducible SQL export:
  `outputs/019fadda-d238-75d3-8312-142dfdce2612/northstar_alpha_reviewed_rule_delta_2026-08-06.sql`.
- Local 25,295-car validation: 10,475 resolved, 12,682 provisional,
  2,138 review-required, 0 failed.
- Review dashboard at `/normalization-review`.
- **A working single-batch normalizer**: the `normalize` CLI job
  (`NormalizeTransportstyrelsenJob`) drives `normalize_batch()`, which reads
  a staging batch with keyset pagination, routes each record, enqueues
  review items, and persists `rule_version`, `mapping_version`,
  `pipeline_version`, status, and timestamps per record.
- **Batch-level job bookkeeping** (`ingestion/job_bookkeeping.py`):
  `claim_job_run` / `complete_job_run` / `fail_job_run` track status, counts,
  and timestamps; re-running a completed batch returns
  `already_completed=True` without reprocessing, and a concurrent claim
  raises `JobAlreadyRunningError`.

The remaining work below is therefore **driving and observing** the existing
normalizer, not building one.

## 2. Coverage risk — decide before production launch

The validated cohort is 41% resolved and **50% provisional** (12,682 of
25,295). Provisional nodes are excluded from customer-facing resolves by
default (see [graph schema design](graph-schema-design.md) §4), so on this
cohort roughly **41% of vehicles would resolve for a customer**, not the 92%
implied by "0 failed".

"0 failed" means the pipeline crashed on nothing — it is not a coverage
measure. Before launch:

- Quantify how much of the provisional bucket TecDoc second-source
  confirmation is expected to promote, and confirm the promotion path is
  actually implemented.
- Agree the minimum customer-facing coverage the first B2B client needs.
- If the provisional share stays near 50% after promotion, that is a product
  decision, not a normalization bug.

## 3. Remaining implementation

### 3.1 Background worker (driver loop)

The per-batch normalizer exists; what is missing is the unattended driver.

- Select pending work (unprocessed staging batches, or new/changed vehicles
  in incremental mode) and call the existing `normalize_batch()` per batch.
- Keep batches bounded; recommended staging batch size 25,000 records. This
  is separate from the pipeline's internal read page size (currently 500).
- Run to completion unattended, with bounded retries and a clean stop signal.
- Status, normalized output, rule version, and processing timestamp are
  already persisted per record — no new storage needed for this item.

### 3.2 Checkpointing — the two pieces that are actually missing

Batch-level resume already works (§1). Missing:

- **Intra-batch cursor.** The keyset position (`after_id`) is a local
  variable, so a crash mid-batch restarts that batch from its beginning.
  Persist the cursor, or confirm and document that per-record persistence is
  idempotent enough to make restart-from-zero safe and merely wasteful.
- **Cross-batch high-water mark.** Persist the last processed stable source
  ID or source-update timestamp. Incremental mode (§3.3) depends on this and
  cannot be built without it.

### 3.3 Incremental processing

- After the initial full backfill, process only new or changed vehicles,
  using the high-water mark from §3.2.
- Reprocess existing vehicles gradually when the active rule version
  changes. Because `rule_version` is stamped per record, records still on an
  older version are directly queryable — use that as the reprocessing work
  queue and as the progress metric.

### 3.4 Manual GitHub Action

- `workflow_dispatch` with `environment`, `mode`, and `batch_size` inputs.
- Modes: `changed-only` and `full-backfill`.
- The action starts or monitors a worker on the application server; it must
  not hold a GitHub-hosted runner busy for an entire backfill.
- Double dispatch is partly protected already by `JobAlreadyRunningError` at
  batch level; confirm that also prevents two concurrent *workers*.

### 3.5 Operational visibility

- Surface current rule version, progress, last checkpoint, processing rate,
  failures, and final totals. Per-batch counts and timestamps already exist
  in job-run rows and structured logs; what is missing is the aggregated
  view.
- Only `review_required` records go to the manual review interface.

### 3.6 Deployment order

Deployment mechanics live in [production deployment](PRODUCTION_DEPLOYMENT.md).
The normalization-specific ordering is:

1. Deploy the Python normalization code.
2. Apply the reviewed SQL rule delta.
3. Verify the expected active rule version.
4. Start the background normalization job.

## 4. Requirements the worker must meet

**Measure throughput before designing the monitoring.** The pipeline is
validated at 25,295 records; the Phase 1 target is 10M — a 400× jump that
has never been timed. Records per second determines whether a backfill is
hours or days, and therefore whether the §3.4 action can sensibly monitor it
at all. Measure on staging first.

**Rule-version rollback.** `rule_version` is stamped per record, so a mixed
population after a bad activation is detectable. The recovery procedure is
not written: how to revert the active version, and how to identify and
reprocess records normalized under the bad one. Write it before the first
production activation.

**Provenance, when graph writes are added.** Normalization currently writes
only to PostgreSQL (normalized results plus review queue) and touches
neither Neo4j nor the enrichment ledger. When the worker is extended to
write graph nodes, the [ledger contract](ledger-schema-design.md) applies in
full: every graph write records a ledger entry, and each logical operation
carries a durable event UUID reused across retries. High-volume workers are
exactly where that gets skipped — make it an explicit requirement of that
future story, not an assumption.

## 5. Data still requiring decisions

2,138 review-required records remain, dominated by ambiguous custom,
one-off, and missing-manufacturer identities. They should stay
restricted/manual unless stronger T12, registered-builder, VIN, or
authoritative manufacturer evidence becomes available.

## 6. Tracking

All tickets sit under EPIC 4 (SCRUM-83).

| Item | Ticket |
|---|---|
| §4 throughput measurement + rollback procedure | SCRUM-163 |
| §3.2 checkpointing (cursor + high-water mark) | SCRUM-164 |
| §3.1 background worker driver | SCRUM-165 |
| §3.4 manual GitHub Action | SCRUM-166 |
| §3.3 incremental + rule-version reprocessing | SCRUM-167 |
| §3.5 operational visibility | SCRUM-168 |
| §2 coverage risk decision | SCRUM-169 |

## Recommended order

SCRUM-163 (throughput + rollback) → SCRUM-164 (checkpointing) → SCRUM-165
(worker driver) → SCRUM-166 (GitHub Action) → SCRUM-168 (visibility) →
SCRUM-167 (incremental). Run one staging backfill before enabling the same
workflow in production. SCRUM-169 (coverage decision) is independent and can
run in parallel, but must be settled before customer launch.
