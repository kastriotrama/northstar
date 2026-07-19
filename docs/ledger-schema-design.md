# Enrichment Ledger Design — Append-Only Provenance

| Field | Value |
|---|---|
| Version | `0.1` |
| Status | Accepted ledger schema contract |
| Owner | NorthStar backend team |
| Jira story | SCRUM-17 (Story 3.2) |
| Scope | `core` schema, `core.enrichment_ledger`, append-only enforcement, correction pattern, provenance rules |
| Last reviewed | 2026-07-19 |

## 1. Purpose and principles

The enrichment ledger is the **append-only provenance record** for every
graph write and enrichment event (Phase 1 plan, Story 3.2). It answers, for
any canonical node: where did this come from, what did each event add, what
did it cost, and how confident were we.

It is also where **conflicting source evidence lives**. Per the accepted
SCRUM-13 relationship contract, the graph holds resolved singular facts —
never parallel edges for provenance or disagreement. When two sources
disagree (231 hp vs 235 hp for the same installation), the resolved value
goes on the graph edge and the competing evidence is recorded here in
`evidence`. SCRUM-68's merge/split procedures record their rationale the
same way.

**Every graph write records a ledger entry.** This is a hard rule for all
future graph-writer code (Epics 5–7): no node or relationship write without
a corresponding `record_ledger_entry` call in the same logical operation.
In Phase 1 most entries record "loaded from TecDoc/Transportstyrelsen" at
cost 0; the schema carries cost so Phase 3 enrichment ROI needs no
migration.

## 2. The `core` schema

Durable operational tables live in `core`, as opposed to `staging`
(disposable raw landings, see
[staging-schema-design.md](staging-schema-design.md)). This story creates
the schema and its first table; Stories 3.3 (`review_queue`) and 3.4
(`ingest_job_runs`) add theirs here too. The same single-application-role
ownership assumptions as staging apply.

## 3. `core.enrichment_ledger` columns

| Column | Type | Nullable | Meaning |
|---|---|---|---|
| `id` | `BIGINT GENERATED ALWAYS AS IDENTITY` | no (PK) | Database-issued ledger entry id; referenced by corrections |
| `event_id` | `UUID` | no (unique) | Caller-generated identity of the logical event. The same value is reused across retries so one event cannot be recorded twice |
| `source` | `TEXT` | no, non-empty | Who asserted this event: `tecdoc`, `transportstyrelsen`, `manual`, later enrichment providers. Pipeline-enforced vocabulary, same convention as Alias `source_system` |
| `target_node_id` | `TEXT` | no, length 30 | The canonical `<PREFIX>-<ULID>` node this entry is about; format validated by the writer via `northstar.is_valid_node_id` |
| `attributes_added` | `TEXT[]` | no, default `{}` | Names of node/edge attributes this event added or resolved. Array, not JSONB: it is a flat list of names queried with containment — no nesting needed |
| `nodes_benefited` | `INTEGER` | no, default 1, ≥ 1 | How many canonical nodes benefit from this event — the shared-component amortization metric (enriching one Engine benefits every variant using it) |
| `cost_eur` | `NUMERIC(12,4)` | no, default 0, ≥ 0 | What the event cost. 0 for Phase 1 bulk loads; real for Phase 3 paid enrichment |
| `confidence` | `DOUBLE PRECISION` | no, 0.0–1.0 | Confidence of the assertion, same scale as Alias confidence |
| `evidence` | `JSONB` | no, default `{}` | Structured evidence: conflicting source values, merge/split rationale, normalization traces. JSONB, not columns: shape varies per event kind and is read as a document, never filtered column-wise |
| `source_batch_id` | `TEXT` | yes | Ties the entry to a staging load batch when applicable |
| `corrects_ledger_id` | `BIGINT` | yes, same-target FK → `id`, unique when present | Set only on compensating correction entries (§5); the database rejects cross-node and branching correction chains |
| `created_at` | `TIMESTAMPTZ` | no, default `now()` | Server-assigned append time |

## 4. Indexes

| Name | Columns | Serves |
|---|---|---|
| `enrichment_ledger_target_node_id_idx` | `target_node_id` | The primary provenance query: full history of one node |
| `enrichment_ledger_created_at_idx` | `created_at` | Time-range reporting and cost roll-ups |

Add further indexes only when a real query needs them.

## 5. Append-only policy — enforced, with a correction pattern

The ledger is append-only **in the database, not just by convention**: the
`enrichment_ledger_append_only` trigger rejects every `UPDATE` and `DELETE`,
and a separate statement-level trigger rejects `TRUNCATE` (row triggers do
not fire on TRUNCATE, so without it one statement could silently erase the
entire history). The `id` column is `GENERATED ALWAYS AS IDENTITY`, so
clients cannot supply their own ids — correction chains can only reference
ids the database actually issued. History cannot be rewritten by
application code, buggy or otherwise.

**The allowed correction pattern:** a wrong entry is corrected by appending
a new compensating entry with `corrects_ledger_id` pointing at the wrong
row, carrying the corrected values and an `evidence` note explaining why.
Readers reconstruct current truth by taking the latest entry in a
correction chain; the original stays visible forever.

Correction chains are linear and node-local. A correction must have the same
`target_node_id` as the entry it corrects, and an entry can be directly
corrected only once. If the correction itself is later wrong, append another
entry that corrects that correction.

## 6. Writing and reading — sanctioned entry points

The only sanctioned write path is `record_ledger_entry`; it validates the
node-id format, confidence range, and count/cost bounds before inserting. The
caller supplies a UUID `event_id` created before the cross-store operation.
Replaying the same event and payload returns the original ledger id; reusing
an event id for different content is rejected.

**Transaction ownership:** `record_ledger_entry` does not commit — the
caller owns the transaction, so a ledger entry commits atomically with any
other Postgres changes of the same logical operation. Standalone callers
commit afterwards.

**Cross-store ordering (Neo4j + Postgres cannot share a transaction):**
generate and durably retain an `event_id`, write the graph, append the ledger
entry with that event id, then commit Postgres.
If the ledger append fails after a successful graph write, the operation
must be retried with the same `event_id` until the entry lands. The unique key
makes ambiguous retries safe: they return the original row instead of adding
an undeletable duplicate. A graph change without provenance is a defect, and
the Epic 10 data-quality report reconciles graph writes against ledger entries
to catch any that slip through.

```python
from decimal import Decimal
from uuid import uuid4
from ingestion.ledger import record_ledger_entry, fetch_entries_for_node

# Generate once before the cross-store operation and retain it for retries.
tecdoc_event_id = uuid4()

# TecDoc load provenance (Phase 1 typical: cost 0, confidence 1.0)
entry_id = record_ledger_entry(
    connection,
    event_id=tecdoc_event_id,
    source="tecdoc",
    target_node_id="ENG-01ARZ3NDEKTSV4RRFFQ69G5FAV",
    confidence=1.0,
    attributes_added=["engine_code", "displacement_cc", "fuel_type"],
    nodes_benefited=1,
    source_batch_id="tecdoc-2026-07-full",
)

# Transportstyrelsen conflict evidence: resolved value went on the graph
# edge; the disagreement is preserved here.
record_ledger_entry(
    connection,
    event_id=uuid4(),
    source="transportstyrelsen",
    target_node_id="VEH-01ARZ3NDEKTSV4RRFFQ69G5FAV",
    confidence=0.78,
    attributes_added=["power_kw"],
    evidence={
        "conflict": {
            "attribute": "power_kw",
            "resolved": 170,
            "competing": [{"source": "transportstyrelsen", "value": 173}],
        }
    },
    source_batch_id="transportstyrelsen-2026-07-batch-12",
)

# Provenance query: full history for one canonical node, oldest first.
entries = fetch_entries_for_node(
    connection, "VEH-01ARZ3NDEKTSV4RRFFQ69G5FAV"
)
```

Equivalent raw SQL for the provenance query:

```sql
SELECT id, source, attributes_added, confidence, evidence, created_at
FROM core.enrichment_ledger
WHERE target_node_id = 'VEH-01ARZ3NDEKTSV4RRFFQ69G5FAV'
ORDER BY id;
```

## 7. Migration runner

```sh
northstar-ingest migrate-ledger
```

Every statement is idempotent (`IF NOT EXISTS` / `CREATE OR REPLACE`);
running the migration twice succeeds and the second run is a no-op. Before
committing, the runner verifies columns, defaults, identity generation,
constraints, indexes, trigger events, trigger functions and enabled state,
raising
`LedgerSchemaContractError` on drift. Statement names below are a stable
contract asserted by the doc contract tests.

| Name | Kind |
|---|---|
| `create_core_schema` | schema |
| `create_enrichment_ledger_table` | table |
| `enrichment_ledger_corrects_once_index` | index |
| `enrichment_ledger_target_node_id_index` | index |
| `enrichment_ledger_created_at_index` | index |
| `enrichment_ledger_append_only_function` | function |
| `enrichment_ledger_append_only_trigger` | trigger |
| `enrichment_ledger_append_only_truncate_trigger` | trigger |

## 8. Review checklist

- [ ] Every new graph-write code path records provenance via
      `record_ledger_entry` in the same logical operation and retains one
      `event_id` across every retry.
- [ ] No code updates or deletes ledger rows; corrections append
      compensating entries with `corrects_ledger_id`.
- [ ] Conflicting evidence goes into `evidence` here, never into parallel
      graph edges (SCRUM-13 contract).
- [ ] New `core` tables (review queue, job runs) follow this document's
      schema-placement and ownership conventions.
- [ ] Migration statements stay idempotent with stable names.
