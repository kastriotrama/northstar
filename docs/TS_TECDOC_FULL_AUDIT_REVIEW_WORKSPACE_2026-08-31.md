# Full TS→TecDoc audit and stakeholder review workspace

## Scope

The local audit is pinned to the approved candidate release manifest and checks all `6,515,471` imported passenger rows against the `72,570`-KType v6 candidate catalog. It uses the active normalization rules, reviewed manufacturer mappings, reviewed model rules, and the existing year, fuel, engine, displacement, power, drive and bodywork conflict gates.

Operation: `095b6dbd-af64-4e36-bfe1-a04543bac5ed`

The run is resumable from its PostgreSQL checkpoint. It records aggregate run counts and one mutually exclusive primary blocker per checked row. It does **not** persist a TS→KType match decision, attach an alias, or write to Neo4j.

## Current verified checkpoint

At the latest committed checkpoint, `175,000 / 6,515,471` rows (`2.686%`) had been checked:

| Result | Rows |
| --- | ---: |
| Resolved | 17,717 |
| Provisional | 15,723 |
| Review required | 125,825 |
| Hard conflict | 13,165 |
| Policy excluded | 2,487 |
| Normalization review | 63 |
| Unmatched | 20 |

The mutually exclusive blocker counts at that checkpoint are:

| Primary blocker | Rows |
| --- | ---: |
| Bodywork conflict | 51,619 |
| Model evidence missing | 44,263 |
| Candidates too close | 13,855 |
| Hard technical conflict | 13,165 |
| Partial or phonetic model | 9,896 |
| Model not found in catalog | 5,660 |
| Model evidence conflicts | 291 |
| Other matcher blocker | 228 |
| Manufacturer scope unresolved | 96 |
| Normalization needs review | 0 |

These are measured checkpoint counts, not an extrapolation to the complete dataset.

## Stakeholder workspace

Open [the local blocker review workspace](http://127.0.0.1:8001/normalization-review?view=match-review). It displays live full-run progress, category totals, real local plate numbers, normalized TS evidence, candidate KTypes, selection evidence, confidence and reason codes.

A bounded sampler exposes representative review items across the blocker categories currently observed. Each item now explains why the gate stopped matching, compares the available TS and TecDoc fields, lists the missing evidence, and asks the stakeholder for the specific decision required. A stakeholder may:

- accept the top candidate when the displayed evidence supports it;
- select another candidate from the evaluator's bounded candidate set;
- keep the vehicle unresolved;
- record a vehicle-only decision or a category-level rule proposal.

Every action requires a reviewer and evidence note. The result is stored as an immutable terminal review resolution. A category proposal is evidence for a later reviewed/versioned rule; it is not applied automatically.

The default workspace is pattern-first. New audit batches write every observed plate-free blocker pattern to `core.match_run_pattern_inventory`, with idempotent batch markers in `core.match_run_pattern_batches` and source-row membership in `core.match_run_pattern_members`; the UI labels these entries `exhaustive`, shows occurrence counts, and lets a stakeholder page through every member vehicle (including plates in the restricted local view). While an older audit process is still running, the endpoint falls back to the bounded review sample until the new writer has populated the inventory. For example, a model-unmatched BMW `IX M60` row is explained as having no manufacturer-scoped catalog candidate above threshold; the decision is whether a reviewed exact alias exists or the vehicle stays unresolved, with the missing approval/family evidence shown alongside the plate-level fields.

Pattern choices are append-only, versioned proposals in `core.match_review_rule_decisions`. `accept_pattern` accepts the displayed mapping for later validation, `keep_blocked` records that the evidence is insufficient, and `change_rule` records corrected target values. None activates a matcher rule or changes PostgreSQL match decisions/Neo4j.

## Safety boundary

The workspace deliberately separates review from promotion. A review action never:

- changes raw or normalized TS evidence;
- weakens the matcher gates;
- inserts a SCRUM-171 match-decision assertion;
- activates a SCRUM-170 alias;
- writes to Neo4j.

Accepted vehicle decisions must still pass immutable-ledger persistence, collision checks, controlled alias attachment and PostgreSQL↔Neo4j reconciliation before graph promotion.

## Resume command

```bash
env PYTHONUNBUFFERED=1 .venv/bin/python -m scripts.audit_local_passenger_match_full \
  --env-file /Users/kastriotrama/Documents/NorthStar/.env \
  --release-manifest ingestion/release_manifests/ts_tecdoc_matcher_candidate_v1_20260831.json \
  --operation-id 095b6dbd-af64-4e36-bfe1-a04543bac5ed \
  --source-prefix normalization-vdai-passenger-full-v323-20260817-part- \
  --expected-source-rows 6515471 \
  --expected-candidates 72570 \
  --code-revision ef926f8+full-review-local \
  --batch-size 25000
```

The same operation ID resumes after the last committed source ID and does not recount completed rows.

## Backfill previously processed rows

If an audit was started before pattern indexing was enabled, backfill the already-processed prefix once (the batch markers make retries safe), then restart/resume the audit so subsequent batches continue recording patterns:

```bash
env PYTHONUNBUFFERED=1 .venv/bin/python scripts/backfill_match_pattern_inventory.py \
  --env-file /Users/kastriotrama/Documents/NorthStar/.env \
  --release-manifest ingestion/release_manifests/ts_tecdoc_matcher_candidate_v1_20260831.json \
  --operation-id 095b6dbd-af64-4e36-bfe1-a04543bac5ed \
  --source-prefix normalization-vdai-passenger-full-v323-20260817-part-
```
