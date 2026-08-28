# Coverage repair: implementation and replay

Follow-up: [immediate package results](TS_TECDOC_IMMEDIATE_PACKAGE_2026-08-28.md)
record the Saab extraction fix, completed 2,284-resolution replay and full
549-case bodywork investigation. The baseline results below are retained.

Branch: `feature/SCRUM-101-integrated-matcher-validation` in `NorthStar-SCRUM-101-integrated`. Local uncommitted work; no push or deployment. The user requested Jira tickets and a 20k re-import after implementation. We reprocess the retained raw rows instead of inserting duplicate staging rows.

## Jira ownership

| Ticket | Scope | Delivery boundary |
|---|---|---|
| SCRUM-172 | Ranked repair cohorts | Private grouping, source availability, top-candidate facts, exact accounting; hypotheses remain unapproved |
| SCRUM-173 | Source-aware comparisons | Comparison machinery and explicit version/hash-pinned reviewed manifest support; no disputed rules activated |
| SCRUM-174 | Model recovery | Fix shared family labels across generations; do not arbitrarily choose a generation or create new alias rules |
| SCRUM-175 | Validation | Same-record replay and changed-resolution evidence; independent held-out adjudication remains required |

These are new subtasks under SCRUM-101. Reuse SCRUM-148/149 ranking/gates, SCRUM-150/171 persistence and SCRUM-170 promotion. No issue statuses were changed.

## Behavioral correction

Previously, `recover_model_from_evidence` required one canonical catalog model. A source label such as V70 could match multiple generations, so recovery returned no model and skipped technical matching entirely.

Recovery now returns the shared explicit family label as a query only if every matching canonical model belongs to that whole-token family prefix. It does not select a generation. A shared trim alias across unrelated families is rejected. Conflicting longest labels are not silently discarded. Existing manufacturer, partial/phonetic, year, fuel, engine, displacement, power, bodywork, margin and candidate-only gates still decide the outcome.

Regression tests cover multiple generations, ambiguity without technical evidence, year-based selection, unrelated trim aliases and conflicting source labels. This is a candidate-generation correction, not an independently verified correctness claim for every newly resolved row.

## Comparison contract

`ingestion/context_comparison.py` supports equivalent, compatible, unknown and conflicting results. Default policy contains no new semantic rules and preserves existing exact bodywork/drive comparisons.

Reviewed compatibility requires field, exact manufacturer/model scope, normalized source value, allowed target values, explicit source-field conditions, reviewer and evidence reference. Compatible comparisons contribute no match bonus. Opposing rules do not union their allowed values; they fail closed. Exact source assertions are not overwritten by broad rules. Raw source conditions participate in the evaluator cache key when rules are supplied, preventing cross-record cache leakage.

Applied rule IDs and policy content hash appear in candidate evidence. No `is_4wd=0` interpretation or AC/estate→SUV rule is activated by this change. Synthetic examples exercise those semantics only inside tests.

The validation command supports an explicit reviewed manifest through all three flags: `--context-policy`, `--context-policy-version`, and `--context-policy-sha256`. The checksum is SHA-256 of Python's `json.dumps(payload, sort_keys=True).encode()` representation. Unknown/mismatched pins, proposed manifests, proposed rules and missing review metadata are rejected. This is a pinned local configuration input, not a new durable approval workflow or database activation. Operator-supplied approval metadata is not an independent correctness verdict.

## Reproduce the retained 20k run

Run from this worktree using the existing Python environment:

```sh
/tmp/northstar-review-20260828/.venv/bin/python scripts/validate_local_matcher_cohort.py \
  --code-root /Users/kastriotrama/Documents/NorthStar-SCRUM-101-integrated \
  --env-file /Users/kastriotrama/Documents/NorthStar/.env \
  --source-prefix normalization-vdai-passenger-full-v323-20260817-part- \
  --catalog-version tecdoc-0326-all-active-candidates-v5-fuel-local \
  --rule-version ts-engine-fingerprint-heldout-v1-disabled-20260824 \
  --expected-candidates 72570 --limit 20000 \
  --baseline-report outputs/scrum101-integrated-20k-20260828.json \
  --output outputs/scrum101-coverage-repair-20k-20260828.json
```

Choose a new output filename for another run: existing evidence cannot be overwritten. The runner verifies source/catalog/rule checksums before matching, uses read-only repeatable-read PostgreSQL, checks that ingestion source code did not change during the run, and reports progress every 100 rows. Normalization remains v6; the shared-family-query recovery and context-policy hashes are separately recorded.

The report contains terminal transitions, changed top KTypes, missing-model source profiles, ranked bodywork/model repair groups, and distinct candidate-only KType counts. Up to three source-evidence examples are retained per group. These include private free text, so the report stays local with mode 0600 and is ignored by Git. Plate/VIN fields are excluded from aggregate diagnostics; that does not make arbitrary source text safe for public sharing.

Replay all changed accepted identities into a separate private packet:

```sh
/tmp/northstar-review-20260828/.venv/bin/python -m scripts.replay_match_repair_evidence \
  --report outputs/scrum101-coverage-repair-20k-20260828.json \
  --env-file /Users/kastriotrama/Documents/NorthStar/.env \
  --output outputs/scrum101-coverage-repair-review-20260828.json
```

This captures the real base/alias matcher attempts, routed alternatives, normalized evidence and catalog facts. Every replay must reproduce the completed cohort outcome. Plate values are included only in this private review packet; verdicts remain null and review status pending.

## Remaining boundaries

Aggregate conflict counts do not prove that a proposed compatibility rule is safe. Full alternative-candidate/counterfactual review of repeated bodywork groups, activation of disputed rules, independent held-out labels, new fingerprint rules, durable ledger orchestration and graph writes are not accomplished merely by creating these tickets or running this cohort. No full-fleet accuracy extrapolation should be made from the retained first 20k.

## Completed 20k results

The exact retained 20,000 rows completed with matching source/catalog/rule checksums and unchanged ingestion code during execution. Zero failed records; no raw inserts, normalized-result writes, match-decision persistence, graph writes or rule activation. Matching plus diagnostics took approximately 650 seconds, excluding initial input loading; this is not a comparison benchmark.

| Terminal | Before | After | Change |
|---|---:|---:|---:|
| Resolved | 1,510 | 2,220 | +710 |
| Provisional | 1,551 | 2,104 | +553 |
| Review-required | 15,519 | 13,967 | -1,552 |
| Hard conflict | 1,307 | 1,596 | +289 |
| Policy excluded | 112 | 112 | 0 |
| Normalization review | 1 | 1 | 0 |
| Total | 20,000 | 20,000 | 0 |

Missing-model results fell 7,905→4,785 (3,120 fewer). There are 716 gained resolutions, six lost resolutions and 14 changed KTypes among still-resolved rows. 701 of the 716 gains previously had missing-model evidence. All 736 affected resolution cases were replayed with captured matcher/router evidence and exactly reproduced the completed report. They remain pending independent review, not approved ground truth.

Five lost resolutions are A3 Sportback rows where the more specific family exposes candidate-margin ambiguity; the sixth is an A3 Sportback that selects a candidate-only target and remains provisional. The 14 changed resolved identities also follow explicit Audi Sportback text rather than the broader A1/A3 catalog family. These observations explain behavior, not independent correctness.

Graph-safety flags increased 1,338→1,893 records, spanning 389 distinct candidate-only KTypes. Of these, 1,846 reach the matcher resolved threshold before graph-safety downgrade (previously 1,290). No targets were promoted.

Bodywork conflict occurrences increased 5,762→6,863 as more rows reached candidate comparison: 6,041 review-required and 822 hard-conflict records. Missing models and bodywork-associated reviews remain the main repair backlog. Margin reviews increased 789→1,236; no-candidate routes fell 677→627. These are further evidence gaps, not reasons to weaken gates.

The top repeated groups now include Volvo XC40 AC/estate→SUV (216 rows: 204 review, 12 hard conflict), Volvo XC60 II (190: 177 review, 13 hard conflict), and Golf VII AC/estate→hatchback (143 review). They require different investigations: an SUV ontology mismatch and potentially choosing hatchback instead of an estate sibling must not be fixed with one broad mapping. Remaining model-missing groups include Saab 9-3/9-5 names; short/numeric family recovery requires a separately tested catalog-scoped correction, not arbitrary number matching.

Validation: 631 unit tests, the 205-case golden corpus, Ruff, strict mypy over 114 files with explicit package bases, and whitespace checks pass. The live read-only 20k replay and 736-case replay validate integration with the retained local PostgreSQL catalog. Schema/Neo4j integration suites were not rerun because this package changes neither persistence schema nor graph writers.

Private artifacts:

- `outputs/scrum101-coverage-repair-20k-20260828.json`
- `outputs/scrum101-coverage-repair-review-20260828.json`
- `outputs/scrum101-bodywork-counterfactual-samples-20260828.json`

Additional diagnostic: three examples from each of the 12 largest bodywork groups (36 rows) were replayed with bodywork withheld only in the hypothetical input, leaving stored/raw facts unchanged. Outcomes were 10 resolved, six provisional, 16 review-required and four hard conflicts; one top candidate changed. This is not a compatibility approval, not a coverage estimate and not 10 newly accepted vehicles. It demonstrates that bodywork removal alone still leaves ambiguity/technical blockers in many examples. No rules or decisions from this diagnostic were activated or persisted.

The plan/tickets, implemented local repair package and requested 20k replay are complete. Broader ticket acceptance remains open for scoped semantic-rule review, new fingerprint validation and independent held-out adjudication. Do not mark the four subtasks Done on the strength of this replay alone.
