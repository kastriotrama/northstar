# Review Queue Design — Normalization Decision Boundary

| Field | Value |
|---|---|
| Version | `0.1` |
| Status | Accepted Phase 1 review-queue contract |
| Owner | NorthStar backend team |
| Jira story | SCRUM-18 (Story 3.3) |
| Scope | `core.review_queue`, candidate shape, worklists, lifecycle, normalization routing |
| Last reviewed | 2026-07-28 |

## 1. Purpose

`core.review_queue` holds source records that must not be written
automatically to the canonical graph because their identity, structure, or
evidence is missing, ambiguous, malformed, or conflicting.

The queue is a safety boundary, not a copy of the source database:

- Raw payloads remain unchanged in the `staging` schema.
- Queue rows point to one raw staging row.
- Candidate matches contain only the evidence needed to make the decision.
- Sensitive registry identifiers and full raw records are not copied into
  `candidate_matches`, `reason_detail`, or `resolution`.
- No fake `Unknown` canonical nodes are created.

## 2. Table contract

| Column | Type | Nullable | Meaning |
|---|---|---|---|
| `id` | `BIGINT GENERATED ALWAYS AS IDENTITY` | no | Internal queue row id |
| `review_id` | `UUID` | no, unique | Caller-issued event id reused across retries |
| `source_system` | `TEXT` | no, non-empty | Source vocabulary, such as `transportstyrelsen` or `tecdoc` |
| `source_batch_id` | `TEXT` | yes | Batch that loaded the referenced staging row |
| `source_table` | `TEXT` | no | Approved raw table: `staging.transportstyrelsen_raw` or `staging.tecdoc_<entity>` |
| `source_record_id` | `BIGINT` | no, ≥ 1 | `id` of the raw staging row |
| `reason_code` | `TEXT` | no, non-empty | Stable machine-readable routing reason |
| `reason_detail` | `TEXT` | yes | Short human explanation without sensitive raw data |
| `target_entity_type` | `TEXT` | yes | Intended canonical concept, for example `Manufacturer` or `VehicleVariant` |
| `candidate_matches` | `JSONB` array | no, default `[]` | Ranked candidate summary defined in §4 |
| `confidence` | `DOUBLE PRECISION` | yes, 0–1 | Best overall normalization confidence; `NULL` when no meaningful score exists |
| `status` | `TEXT` | no, default `pending` | Controlled lifecycle value defined in §3 |
| `resolution` | `JSONB` object | no, default `{}` | Structured terminal decision |
| `resolved_by` | `TEXT` | yes | Reviewer/service identity; required for terminal states |
| `created_at` | `TIMESTAMPTZ` | no | Queue creation time |
| `updated_at` | `TIMESTAMPTZ` | no | Last lifecycle transition time |
| `resolved_at` | `TIMESTAMPTZ` | yes | Required for `resolved` and `rejected` |

The database checks the source-table pattern, candidate JSON type, confidence
range, status vocabulary, resolution state, and timestamp ordering.

## 3. Status lifecycle

```text
pending ────────> in_review ────────> resolved
   │                  │
   │                  ├─────────────> rejected
   │                  └─────────────> pending
   ├────────────────────────────────> resolved
   └────────────────────────────────> rejected
```

- `pending`: waiting in the unassigned worklist.
- `in_review`: actively being investigated.
- `resolved`: a documented normalization decision was accepted. Reprocessing
  may now apply the accepted rule or selected candidate.
- `rejected`: no canonical write is allowed for this review event.

`resolved` and `rejected` are terminal through the sanctioned API. A later
rule change does not rewrite the historical decision; reprocessing creates a
new review event if another decision is needed.

Every terminal transition requires `resolved_by`, `resolved_at`, and a
non-empty `resolution` object.

## 4. Candidate-match JSON shape

`candidate_matches` is always an array. Each element written through the
sanctioned queue helper has this shape:

```json
{
  "candidate_reference": "MFR-01ARZ3NDEKTSV4RRFFQ69G5FAV",
  "candidate_type": "Manufacturer",
  "confidence": 0.82,
  "evidence": {
    "matched_fields": ["brand"],
    "conflicting_fields": ["base_vehicle_manufacturer"]
  }
}
```

- `candidate_reference` is an opaque canonical id when one exists. Before a
  canonical node exists it may be a source-scoped reference, never a raw
  personal identifier.
- `candidate_type` names the proposed canonical concept.
- `confidence` is the score for this candidate, from 0 to 1.
- `evidence` explains matched, missing, or conflicting non-sensitive fields.

The array may be empty when no candidate can be produced. Candidate order is
best match first. Pipeline code must not treat the first candidate as
accepted until the queue row is resolved.

## 5. Raw-record reference strategy

Queue rows store:

```text
source_system + source_batch_id + source_table + source_record_id
```

The reviewer service uses `source_table` and `source_record_id` to retrieve
the immutable raw staging payload under the source-data access policy.
`source_batch_id` supports lineage and later reprocessing.

There is deliberately no cross-table foreign key: TecDoc uses the
`staging.tecdoc_<entity>` pattern and future entities are added over time.
The migration and writer restrict references to that pattern, while the
normalization transaction must verify that the row exists before enqueueing.

## 6. Routing from the normalization gate

Confidence is useful but does not overrule a hard conflict.

| Route | Default condition | Result |
|---|---|---|
| Canonical graph | Confidence ≥ `0.90`, required identity present, no hard conflict | Write only accepted facts and append provenance to `core.enrichment_ledger` |
| Provisional | Confidence ≥ `0.70` and < `0.90`, identity is stable, uncertainty concerns optional facts only | Create/update the stable identity with accepted facts; omit uncertain facts and record provenance |
| Review queue | Confidence < `0.70`, no meaningful score, ambiguous identity/structure, malformed required data, or any hard conflict | Do not write the disputed canonical fact; enqueue the raw reference and candidates |

The numerical thresholds are Phase 1 defaults and must be configuration,
not scattered constants. A deterministic accepted translation rule can
override a low statistical score. Conversely, the following hard stops
always route to review regardless of score:

- two or more plausible identities cannot be separated;
- manufacturer and base-vehicle manufacturer roles are unresolved;
- a required source value is malformed;
- sources disagree about a singular canonical relationship;
- the selected mapping would create or merge a canonical identity without
sufficient evidence.

The executable composite calculation, exact boundary behavior and immutable
decision persistence are defined in
[confidence-routing-contract.md](confidence-routing-contract.md).

Missing optional component information is omitted. It does not create an
`Unknown` Engine, Transmission, Bodywork, or Platform.

## 7. Sanitized worked examples

### Manufacturer versus bodybuilder

A registry row identifies the marketed brand as `OPEL`, while the
base-vehicle manufacturer is a Peugeot legal entity. If the separate TS
`Tillverkare` organization has not yet been classified as vehicle
manufacturer, bodybuilder, or converter:

```text
reason_code: manufacturer_role_unknown
target_entity_type: Manufacturer
result: no automatic Manufacturer selection; queue for review
```

Once the organization dictionary classifies `Tillverkare`, reprocessing uses
the accepted precedence rule. Both original manufacturer roles remain in
staging and provenance.

### Petrol code plus explicit hybrid evidence

A registry row has primary fuel `petrol` but also carries the explicit
`ELHYBRID` emission/configuration marker and electric-motor power.

- Before an accepted powertrain rule exists, contradictory simplified
  classification routes as `powertrain_signal_conflict`.
- After the rule explicitly gives the TS hybrid marker precedence over the
  primary combustion-fuel code, the record normalizes deterministically as
  petrol-electric hybrid and no longer enters review.

This demonstrates that the queue is not a permanent home for every unusual
record. Accepted rules should reduce repeated review through reprocessing.

## 8. Queue operations

The sanctioned Python operations are:

- `enqueue_review_item`: idempotent insert by `review_id`;
- `fetch_review_items_by_status`: stable oldest-first worklist query;
- `transition_review_item`: controlled lifecycle transition with terminal
  decision metadata.

The caller owns the transaction. Enqueueing should occur atomically with any
PostgreSQL normalization bookkeeping.

Common SQL worklist:

```sql
SELECT id, reason_code, target_entity_type, confidence, created_at
FROM core.review_queue
WHERE status = 'pending'
ORDER BY created_at, id
LIMIT 100;
```

The composite `review_queue_status_created_at_idx(status, created_at, id)`
serves pending, in-review, resolved, and rejected worklists. The
`review_queue_source_record_idx(source_table, source_record_id)` finds prior
decisions for one raw record.
`review_queue_source_batch_status_idx(source_batch_id, status, updated_at, id)`
keeps batch-filtered review worklists fast as the queue grows.

## 9. Migration runner

```sh
northstar-ingest migrate-review-queue
```

The migration is idempotent and verifies the full table, constraint, and
index contract before committing.

| Statement | Kind |
|---|---|
| `create_core_schema` | schema |
| `create_review_queue_table` | table |
| `review_queue_status_created_at_index` | index |
| `review_queue_source_record_index` | index |
| `review_queue_source_batch_status_index` | index |

## 10. Review checklist

- [ ] Raw payloads remain in staging and are referenced, not copied.
- [ ] Candidate evidence contains no plate, VIN, owner, or full raw payload.
- [ ] Hard conflicts override confidence and prevent disputed graph writes.
- [ ] Provisional writes omit uncertain facts and record provenance.
- [ ] Terminal decisions identify the reviewer and structured resolution.
- [ ] Accepted rules trigger reprocessing so equivalent records do not need
      repeated manual review.
