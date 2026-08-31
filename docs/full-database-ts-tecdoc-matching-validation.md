# Full-database TS-to-TecDoc matching validation

## Purpose

Validate the complete Transportstyrelsen-to-TecDoc passenger-car matching flow
against the full source before it is automated by a production background
worker. The run must be reproducible, checkpointed, observable, and safe to
resume.

This work validates normalization, candidate matching, immutable decision
persistence, PostgreSQL-to-Neo4j reconciliation, and controlled promotion. It
does not approve broader normalization rules or attach uncertain aliases.

## Jira scope and dependency map

This validation composes the capabilities owned by two existing Jira issues:

| Jira issue | Responsibility in this validation |
|---|---|
| `SCRUM-171` | Dry-run/persist boundary, immutable match decisions, one current decision head per stable plate/source version, idempotent retries, and linear supersession history |
| `SCRUM-170` | Read current resolved decision heads, preflight KType and alias integrity, reconcile PostgreSQL with Neo4j, and perform controlled or production promotion |

Required execution order:

```text
Normalize TS source
  -> generate and route TecDoc candidates
  -> SCRUM-171 dry-run validation
  -> approve and persist immutable decisions
  -> reconcile decision heads and graph state
  -> SCRUM-170 dry-run promotion preflight
  -> controlled resolved-only promotion
  -> final reconciliation
```

`SCRUM-170` depends on `SCRUM-171`: promotion must never consume an ad hoc
matcher result or a non-current decision. The full-database runner coordinates
both stories but must not duplicate their persistence or graph-write logic.

The live Jira issue links could not be verified on 2026-08-18 because the
Atlassian connector failed at its transport boundary. Confirm the formal Jira
links before changing either ticket.

## Work status

| Work item | Status | Owner/story |
|---|---|---|
| Immutable match decision lifecycle | Implemented in repository; full-data verification pending | `SCRUM-171` |
| Controlled KType promotion and reconciliation | Implemented in repository; controlled full-data verification pending | `SCRUM-170` |
| Full-cohort orchestration runner | Write-free injected loop implemented; real TS/TecDoc adapters and CLI pending | New validation/runner ticket |
| Durable matching checkpoints and safe resume | Schema, immutable-pin claim, monotonic checkpoint and resume contracts implemented | New validation/runner ticket |
| Full-database dry-run report | Not started | New validation/runner ticket |
| Persisted full-database decision run | Blocked by approved dry-run report | New validation/runner ticket |
| Controlled promotion | Blocked by persisted decisions and clean reconciliation | `SCRUM-170` |
| Production background worker and GitHub Action | Follow-up after validation | Separate operations ticket |

## Implementation checklist

### A. Full-cohort runner

- [ ] Add a dedicated TS-to-TecDoc matching command with `dry_run` and
      `persist` modes.
- [ ] Require explicit source, normalization-rule, TecDoc-catalog, confidence-
      policy, and code versions.
- [ ] Refuse unversioned or mutable inputs.
- [ ] Read only normalization-eligible rows; retain normalization review and
      policy exclusions separately.
- [ ] Reuse the existing manufacturer index, fuzzy matcher, confidence router,
      and SCRUM-171 persistence service.
- [ ] Produce deterministic candidate and decision payloads.

### B. Checkpoints and resume

- [x] Add a durable run manifest keyed by a collision-safe operation UUID.
- [x] Store bounded batch checkpoints and cumulative counters.
- [x] Reuse the same operation ID and pinned versions on retry.
- [x] Reject resume when any pinned input differs.
- [ ] Test interruption before commit, after PostgreSQL commit, and during
      restart.

### C. Reporting

- [ ] Report normalization, eligibility, matching-route, unmatched, and
      hard-conflict totals separately.
- [ ] Reconcile every input row into exactly one terminal accounting category.
- [ ] Store safe representative examples for each reason without sensitive raw
      payloads in logs.
- [ ] Expose current checkpoint, throughput, elapsed time, and last failure.

### D. Persistence and promotion gates

- [ ] Prove dry-run creates no decision, head, supersession, alias, or graph
      writes.
- [ ] Require explicit approval before switching the same pinned run to
      `persist`.
- [ ] Verify one current decision head per stable plate/source version.
- [ ] Block a plate selecting multiple KTypes.
- [ ] Run SCRUM-170 reconciliation before promotion.
- [ ] Promote at most 1,000 resolved decisions in the first controlled cohort.
- [ ] Prove controlled replay is idempotent and reconcile again.

### E. Verification

- [ ] Add unit tests for version pins, accounting, batching, and resume rules.
- [ ] Add PostgreSQL integration tests for retry ambiguity and current-head
      uniqueness.
- [ ] Add Neo4j integration tests for alias conflicts and resolved-only writes.
- [ ] Run Ruff, strict mypy, focused tests, and the full relevant test suite.
- [ ] Record the final commands, manifest, totals, and remaining risks here.

## Pinned inputs

Record these values in the run report before processing starts:

| Input | Required value |
|---|---|
| Eligible passenger TS rows | `6,515,471` |
| Normalization rule version | `ts-review-20260817T073842135705Z` |
| TecDoc catalog | Exact immutable catalog version and KType count |
| Confidence policy | Exact policy version and thresholds |
| Source batch | Stable import prefix and source version |
| Code revision | Git commit SHA used by the worker |

Stop the run if the source count, active rule version, catalog version, or
catalog count differs from the approved values.

## Safety invariants

- Look up and reconcile existing source assertions before minting identifiers.
- Maintain one current decision head per plate and source version.
- A plate must never select multiple KTypes in the same current version.
- Dry-run matching must not persist decisions or mutate Neo4j.
- Only current `resolved` decision heads may be promoted.
- Never attach aliases for `provisional`, `review_required`, unmatched, or
  hard-conflict results.
- A replay with identical input and versions must be idempotent.
- Preserve previous immutable decisions when a newer decision supersedes them.
- Stop a promotion batch completely when an alias or KType preflight conflict
  is detected.

## Required implementation before the full run

The repository has the confidence-routing persistence and controlled-promotion
primitives, but the production orchestration must provide:

- a full-cohort TS-to-TecDoc matching runner;
- bounded batches with durable checkpoints;
- safe resume using the same source, catalog, rule, and policy versions;
- explicit `dry_run` and `persist` modes;
- per-batch and cumulative metrics;
- structured failure records without sensitive source payloads;
- a final reconciliation and run manifest.

The runner must use the existing confidence-routing and match-promotion
contracts rather than implementing a second decision path.

## Execution sequence

### 1. Preflight infrastructure

Verify PostgreSQL, Neo4j, Redis, and Elasticsearch health. Confirm sufficient
disk space and create a unique run ID. Do not reuse a partially completed run
ID with different pinned inputs.

Apply the confidence-routing schema idempotently:

```bash
northstar-ingest migrate-confidence-routing
```

Apply the version-pinned run manifest and checkpoint schema:

```bash
northstar-ingest migrate-match-runs
```

Verify the decision tables, constraints, indexes, and immutability triggers are
present and enabled.

### 2. Verify source and catalog

Confirm exactly `6,515,471` passenger-eligible TS rows using the accepted
`M1`/`M1G`, with `PB` fallback only when EU category is absent, policy.

Verify the complete TecDoc catalog and record its immutable version, KType
count, source checksum, and load timestamp. Do not silently use the newest
catalog if it differs from the pinned version.

### 3. Normalize TS rows

Load the immutable rule version
`ts-review-20260817T073842135705Z` explicitly. Process the source in bounded,
checkpointed batches and report:

- processed;
- resolved;
- provisional;
- review-required;
- failed;
- policy-excluded;
- last successful checkpoint.

Normalization review cases remain normalization review cases. TecDoc matching
must not be used to bypass an unresolved normalization safety gate.

### 4. Run matching in dry-run mode

Generate TecDoc candidates and route confidence decisions without database or
graph writes. Report cumulative and per-batch counts for:

```bash
northstar-ingest match-ts-tecdoc \
  --operation-id <operation-uuid> \
  --source-version <immutable-ts-source-version> \
  --source-batch-prefix <normalized-batch-prefix> \
  --expected-source-rows 6515471 \
  --normalization-rule-version ts-review-20260817T073842135705Z \
  --candidate-catalog-version <immutable-tecdoc-version> \
  --candidate-source postgres \
  --expected-ktype-count 72570 \
  --policy-version confidence-routing-v1 \
  --context-policy ingestion/reviewed_context_policies/volvo_bodywork_reviewed_v1_20260830.json \
  --context-policy-version volvo-bodywork-reviewed-v1-20260830 \
  --context-policy-sha256 4acdee26fb88c639fa29cae914e7d24bc067b963e0be65a4718a5036d2ea522a \
  --code-revision <git-commit-sha> \
  --page-size 25000
```

The command currently supports dry-run only. It loads the KType catalog from
the explicitly selected source, verifies its pinned count, loads an optional
reviewed context policy only when manifest/version/SHA pins all agree, reads
only the selected normalization rule version from PostgreSQL, and writes only
run manifests, checkpoints, and
sanitized aggregate reason counts in `core.match_run_reason_counts`. Reason
aggregates are committed atomically with each checkpoint and contain no plate,
VIN, credential, or raw payload. It does not persist SCRUM-171 decisions or
mutate graph aliases.

- resolved;
- provisional;
- review-required;
- manufacturer unmatched;
- manufacturer conflicted;
- model evidence missing;
- no candidate above threshold;
- hard manufacturer, model-series, year, fuel, engine, displacement, and power
  conflicts.

Retain representative evidence samples for every route and conflict reason.
Do not include VINs, plates, credentials, or full raw provider payloads in
ordinary logs.

### 5. Review the dry-run gate

Before persistence, confirm:

- totals reconcile to the eligible input count;
- no route bypasses a hard conflict;
- resolved decisions meet the configured threshold and candidate-margin gate;
- provisional and review decisions remain unselected for promotion;
- rerunning a fixed sample produces byte-equivalent decision payloads.

Persistence requires explicit approval after this report is reviewed.

### 6. Persist immutable decisions

Rerun the same pinned cohort in `persist` mode. Persist decisions and advance
decision heads atomically. Resume only from durable checkpoints.

Verify:

- one current head exists per stable plate/source version;
- identical retries return the existing immutable decision;
- changed catalog or policy versions create a new decision and a linear
  supersession edge;
- no current plate/source version selects multiple KTypes;
- no review-required decision stores a selected candidate.

### 7. Reconcile PostgreSQL and Neo4j

Run reconciliation before promotion. At this point Neo4j should not contain
new TS aliases from the dry-run or decision-persistence phases.

The reconciliation report must identify:

- missing decision assertions;
- multiply targeted aliases;
- divergent decision IDs;
- graph aliases without current PostgreSQL decision provenance;
- current resolved decisions whose KType target is missing or ambiguous.

Any discrepancy blocks promotion.

### 8. Controlled promotion

Run promotion preflight in `dry_run` mode, then promote a controlled cohort of
at most 1,000 current resolved decisions. Verify the cohort manually and rerun
it to prove idempotency.

Only after the controlled cohort and reconciliation pass may production
promotion begin. Promotion must exclude provisional, review-required,
unmatched, hard-conflict, superseded, and non-current decisions at the query
boundary.

### 9. Final reconciliation and report

After promotion, reconcile PostgreSQL and Neo4j again and publish:

```json
{
  "run_id": "...",
  "source_rows": 6515471,
  "rule_version": "ts-review-20260817T073842135705Z",
  "tecdoc_catalog_version": "...",
  "policy_version": "...",
  "normalization": {
    "resolved": 0,
    "provisional": 0,
    "review_required": 0,
    "failed": 0,
    "excluded": 0
  },
  "matching": {
    "resolved": 0,
    "provisional": 0,
    "review_required": 0,
    "unmatched": 0,
    "hard_conflict": 0
  },
  "promotion": {
    "eligible": 0,
    "promoted": 0,
    "already_consistent": 0,
    "blocked": 0
  },
  "reconciliation_defects": 0,
  "last_checkpoint": "..."
}
```

All category totals must reconcile. The report must state whether the run is
complete, safely paused, or blocked; a stopped worker must never be reported as
complete.

## Completion criteria

- All `6,515,471` eligible TS rows are accounted for.
- Every run artifact records the pinned input and code versions.
- Dry-run is proven write-free.
- Persisted retries are idempotent.
- There is one current decision head per plate/source version.
- No plate selects multiple current KTypes.
- PostgreSQL and Neo4j reconcile with zero unexplained defects.
- A controlled resolved cohort promotes and replays successfully.
- No provisional, review-required, unmatched, or conflicted alias is attached.
- Checkpoint resume is tested after an intentional interruption.
- Progress, failures, and reconciliation defects are visible to operators.

## Follow-up

After this validation succeeds, build the production background worker and
GitHub Action around the same version pins, checkpoints, reports, and safety
gates. Automation must not weaken or duplicate this workflow.
