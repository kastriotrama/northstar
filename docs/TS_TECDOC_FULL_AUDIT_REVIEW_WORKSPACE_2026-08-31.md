# Full TS→TecDoc audit and stakeholder review workspace

## Scope

The local audit is pinned to the approved candidate release manifest and checks all `6,515,471` imported passenger rows against the `72,570`-KType v6 candidate catalog. It uses the active normalization rules, reviewed manufacturer mappings, reviewed model rules, and the existing year, fuel, engine, displacement, power, drive and bodywork conflict gates.

Operation: `095b6dbd-af64-4e36-bfe1-a04543bac5ed`

The run is resumable from its PostgreSQL checkpoint. It records aggregate run counts and one mutually exclusive primary blocker per checked row. It does **not** persist a TS→KType match decision, attach an alias, or write to Neo4j.

## Current verified checkpoint

At the first committed checkpoint, `25,000 / 6,515,471` rows (`0.384%`) had been checked:

| Result | Rows |
| --- | ---: |
| Resolved | 3,097 |
| Provisional | 2,508 |
| Review required | 17,276 |
| Hard conflict | 1,976 |
| Policy excluded | 142 |
| Normalization review | 1 |

The mutually exclusive blocker counts at that checkpoint are:

| Primary blocker | Rows |
| --- | ---: |
| Bodywork conflict | 7,282 |
| Model evidence missing | 5,454 |
| Candidates too close | 2,236 |
| Hard technical conflict | 1,976 |
| Partial or phonetic model | 1,481 |
| Model not found in catalog | 766 |
| Model evidence conflicts | 31 |
| Other matcher blocker | 26 |
| Manufacturer scope unresolved | 1 |
| Normalization needs review | 0 |

These are measured checkpoint counts, not an extrapolation to the complete dataset.

## Stakeholder workspace

Open [the local blocker review workspace](http://127.0.0.1:8001/normalization-review?view=match-review). It displays live full-run progress, category totals, real local plate numbers, normalized TS evidence, candidate KTypes, selection evidence, confidence and reason codes.

A bounded sampler has populated 77 representative review items across the blocker categories currently observed. A stakeholder may:

- accept the top candidate when the displayed evidence supports it;
- select another candidate from the evaluator's bounded candidate set;
- keep the vehicle unresolved;
- record a vehicle-only decision or a category-level rule proposal.

Every action requires a reviewer and evidence note. The result is stored as an immutable terminal review resolution. A category proposal is evidence for a later reviewed/versioned rule; it is not applied automatically.

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
