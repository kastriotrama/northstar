# SCRUM-164–175 recovery status

Date: 2026-08-30  
Branch: `feature/SCRUM-101-integrated-matcher-validation`  
PR: [#32](https://github.com/kastriotrama/northstar/pull/32)

## Verified baseline

- The retained development cohort reconciles to exactly 20,000 records:
  2,284 resolved, 2,218 provisional, 13,788 review-required, 1,597 hard
  conflicts, 112 policy exclusions, one normalization-review record, and zero
  failures.
- The independently separated holdout is frozen at 11,629 rows / 11,107
  leakage-separated groups and has intentionally not been scored.
- The full local raw source contains 6,515,471 passenger records. No result
  from the 20,000-record development cohort is a national accuracy estimate.
- The candidate source audit covers 72,570 TecDoc KTypes. This does not prove
  that the promoted Neo4j catalog contains or can resolve all of them.
- PR #32 validation on 2026-08-30 passed compilation, Ruff, mypy for 113
  source files, 205 golden cases, and all 813 tests after replacing one stale
  hard-coded v5 integration-test expectation with the canonical v6 pipeline
  constant. A synthetic merge of that fix with current `origin/develop` passed
  the same gates, so the locally tested merge result is clean.

## Jira acceptance audit

All tickets below were `To Do` and unassigned when first checked on 2026-08-30.
After PR #32 passed GitHub CI, SCRUM-172–175 were moved to `In Progress`
with ticket-specific evidence comments. This Jira workflow has no `In Review`
state, so none was marked `Done`; SCRUM-164–171 were left unchanged.

| Ticket | Evidence in the repository | Remaining acceptance work | Recommended status after PR review |
| --- | --- | --- | --- |
| SCRUM-164 | Match-run and remote-import checkpoints exist, but `normalize_batch` still holds its `after_id` cursor only in memory. | Add or explicitly prove safe normalization intra-batch resume; persist a cross-batch high-water mark; interrupt/resume integration test. | To Do |
| SCRUM-165 | Bounded per-batch normalization and duplicate-run protection exist. | Add an unattended multi-batch driver, retry/stop behavior, worker locking, and restart integration coverage. | To Do |
| SCRUM-166 | The deploy workflow has a generic manual trigger. | Add a normalization-specific dispatch with environment, `changed-only`/`full-backfill`, batch size, server-side worker launch/monitoring, and concurrency protection. | To Do |
| SCRUM-167 | Per-record rule/pipeline versions are stored. | Add high-water-mark changed-only selection and a gradual queue for rows on superseded rule versions, with remaining-count reporting. | To Do |
| SCRUM-168 | Per-batch counters/logs and review UIs exist. | Add an operator-facing logical-run view with active version, aggregate progress, checkpoint, rate, failures, and final totals. | To Do |
| SCRUM-169 | Coverage and route categories are measured on controlled cohorts. | Replace the obsolete 25,295-row assumption with current validated evidence; agree minimum precision, coverage, and review SLA plus launch/restrict/invest decision. | To Do / product decision |
| SCRUM-170 | Promotion contract, controlled limit, preflight/reconciliation code, and integration tests exist on `develop`. | Approve the evidence contract; execute and document a real controlled cohort; prove replay and PostgreSQL/Neo4j reconciliation before production mode. | In Progress |
| SCRUM-171 | Immutable decision, head, idempotency, and linear supersession persistence plus tests exist on `develop`. | Run an approved matcher policy in persist mode over a controlled real cohort, verify complete evidence, restart safety, and zero alias writes during persistence. | In Progress |
| SCRUM-172 | PR #32 contains pinned cohort diagnostics, ranked repair groups, immutable private reports, graph-ineligible separation, and regression tests. | Reviewer maps the PR files/results to every criterion and confirms private evidence handling. | In Progress; ready for review |
| SCRUM-173 | PR #32 implements four-state source-aware comparison, scoped/versioned rules, fail-closed behavior, evaluator wiring, safety tests, and disclosed 20k deltas. | Domain approval remains required before disputed semantic mappings can be activated, but is not evidence that the engineering contract failed. | In Progress; ready for review |
| SCRUM-174 | PR #32 partitions recovery cohorts, fixes deterministic extraction defects, records scoped proposals/counterexamples, preserves technical gates, and freezes leakage-separated validation groups. | Independent reviewers must approve or reject proposed semantic mappings before activation. | In Progress; ready for review |
| SCRUM-175 | PR #32 preserves a pinned 20k comparison, changed-outcome packets, checksums, and a frozen holdout. | Complete independent adjudication, freeze the final approved policy, score the holdout once, and compare precision/coverage against the SCRUM-169 risk target. | In Progress |

## Critical path

1. Push the locally green PR #32 fix after approval, let GitHub CI pass, obtain
   review, and validate the PR merge result against current `develop`.
2. Independently adjudicate the Golf source fingerprints, 47 Volvo proposals,
   and the mixed-fuel/multi-engine representation contract. Do not activate a
   blanket Golf Variant mapping or collapse `Petrol/Alcohol` to one scalar.
3. Publish an immutable approved rule manifest containing scope, reviewer,
   evidence reference, version, and checksum. Proposed/rejected rules stay
   inactive.
4. Rerun the same pinned 20,000 rows. Reconcile every gained/lost resolution,
   changed KType, route transition, conflict, and graph-ineligible outcome.
5. Score the frozen holdout once with the final policy. Keep `unsure` explicit
   and require the SCRUM-169 precision/coverage gate before persistence.
6. Implement SCRUM-164, then SCRUM-165. SCRUM-166–168 can follow on their own
   story branches while domain review is running.
7. Persist a controlled cohort through SCRUM-171, then promote and attach
   aliases through SCRUM-170. Reconcile PostgreSQL and Neo4j with zero
   unexplained divergence before expanding from a small cohort to 1,000.
8. Only then run all 6,515,471 rows using immutable source, catalog, rule, and
   code pins and publish exact totals by route and conflict reason.

## Stop conditions

- A lower review-required count is not acceptance evidence by itself.
- Candidate-derived engine allocations are not independent TS engine evidence.
- Compatible bodywork or drive facts do not receive a positive match bonus.
- A provisional or candidate-only KType is not customer-resolvable.
- No production alias may be attached before the current decision, KType
  promotion state, plate uniqueness, and cross-datastore reconciliation pass.
