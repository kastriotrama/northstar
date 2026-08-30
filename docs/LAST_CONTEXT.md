# Last Context

Keep the latest 10 task entries only.

## 2026-08-30 — PR #32 green and Jira acceptance audit

- Replaced the stale `normalization-pipeline-v5` integration assertion with the canonical pipeline-version constant and pushed commit `f88d5b1` to PR #32. Local branch and synthetic-merge validation passed compilation, Ruff, mypy for 113 source files, all 205 golden cases, and all 813 tests; GitHub CI run 33306864077 passed both jobs. Audited SCRUM-164–175 and moved only directly worked SCRUM-172–175 to In Progress because the Jira workflow has no In Review state, adding ticket-specific evidence and remaining-risk comments. SCRUM-164–171 were left unchanged. See `docs/SCRUM_164_175_RECOVERY_STATUS_2026-08-30.md`. No rule activation, match-decision persistence, alias attachment, Neo4j write, PR merge, or Jira Done transition occurred.

## 2026-08-28 — PR #32 published

- Committed the integrated matcher, independent approval evidence tooling, mixed-fuel evidence model, full-source audit, tests and gate documentation as `b7b267e`, pushed `feature/SCRUM-101-integrated-matcher-validation`, and opened [PR #32](https://github.com/kastriotrama/northstar/pull/32) targeting `develop`. Local validation passed: 727 unit tests, focused evidence tests, Ruff, strict mypy, compile and diff checks. GitHub checks are running (`images` in progress, `backend` queued). No catalog rebuild, rule activation, decision persistence, alias attachment or Neo4j mutation was performed.

## 2026-08-28 — Independent approval evidence and mixed-fuel gate

- Added read-only RDW exact approval/variant/version/revision evidence joins for the 143 Golf proposals: 66 AU, 59 AUV, 16 CD and 2 CDV rows; all independently join to Golf and body code AC, but this does not identify a TecDoc KType or justify a blanket Golf Variant rule. Reviewed TÜV evidence showing AUV is shared by Golf R Variant and Golf Sportsvan. Added a full 72,570-KType source audit: 2,332 active engine relationships use official KT088 `Petrol/Alcohol` (026); all 11 candidate-only targets are present, nine have unique source displacement, and mixed fuel remains non-promotable. Implemented immutable `EngineFuelEvidence`, PostgreSQL relationship/candidate persistence, scalar-fuel guard, and regression/integration coverage. 727 unit tests, Ruff, strict mypy and diff checks pass. No commit, push, catalog rebuild, decision persistence, alias activation, Neo4j mutation or Jira status change. See `docs/TS_TECDOC_NEXT_GATES_EVIDENCE_2026-08-28.md`.

## 2026-08-28 — Scoped source-model policy and promotion readiness

- Added pinned, reviewed source-context family-query support to the real evaluator, default disabled; exact 20k replay preserved all evaluations (2,284 resolved / 13,788 review). Golf's 143 cases have four source types and zero qualifying strict full-fingerprint proposals. Audited 114 provisional cars → 11 absent graph KTypes: 113 blocked by mixed Petrol/Alcohol engine fuel, one by two engine allocations; complete-source displacement verification remains. Prepared 47 unapproved Volvo rules covering 403 records; froze 11,629 unscored holdout rows from the next 50k after group-leakage exclusions. 699 unit tests, 205 golden, Ruff/mypy pass. Jira progress comments only; no push/datastore writes/rule activation. See `docs/TS_TECDOC_SCOPED_RECOVERY_2026-08-28.md`; independent approval remains the next gate.

## 2026-08-28 — Immediate Saab/bodywork repair package

- Fixed catalog-scoped Saab 9-3/9-5 hyphenated-name extraction; same 20k resolved 2,220→2,284, provisional 2,104→2,218, review 13,967→13,788, hard conflicts 1,596→1,597, missing models 4,785→4,312. No lost resolutions or changed previously resolved KTypes; all 64 gains replayed. Full 549-case bodywork sibling evidence shows Golf estates are present but partial model matches; no disputed rules activated. Audited prior 736 changes, flagging 24 priority independent reviews. 670 unit tests, 205 golden cases, Ruff/mypy/diff checks pass. See `docs/TS_TECDOC_IMMEDIATE_PACKAGE_2026-08-28.md`. No push, datastore writes or status transitions; independent adjudication and held-out validation remain required.

## 2026-08-28 — Coverage repair, Jira and completed 20k replay

- Created SCRUM-172–175 under SCRUM-101; added execution map. Implemented source-scoped four-state comparison support (no disputed rules activated), fixed explicit family recovery across multiple catalog generations, and added private cohort/replay evidence. Same 20k: resolved 1,510→2,220, provisional 1,551→2,104, review 15,519→13,967, hard conflicts 1,307→1,596, missing models 7,905→4,785; zero failures. Replayed all 736 changed-resolution cases (716 gains, 6 losses, 14 changed KTypes), pending independent review. 631 unit tests, 205 golden cases, lint/type checks pass. See `docs/TS_TECDOC_REPAIR_EXECUTION_2026-08-28.md`. No push, DB/graph writes or issue-status changes; semantic approval and held-out validation remain open.

## 2026-08-28 — Coverage-first matcher plan

- Rechecked current code, 20k artifacts and Jira contracts; no implementation or Jira mutations. Missing-model (7,905) and bodywork-associated review population (5,053) are disjoint and cover 83.5% of reviews; 1,290 separate provisional records pass resolved scoring but fail candidate-only graph eligibility. Added `docs/TS_TECDOC_COVERAGE_PLAN_2026-08-28.md`: ranked cohort diagnostics, source-aware compatibility, scoped model recovery, distinguishing evidence, independent validation, then ledger/graph delivery. Counts recomputed from artifact; no guaranteed uplift or full-fleet extrapolation. Next implement the focused repair package after authorization.

## 2026-08-28 — SCRUM-101 matcher work reuse

- Removed redundant unit-weight bodywork rescoring and added bounded, policy/catalog-keyed model-label comparison caching; technical evidence and decisions are not cached by the new helper. Read-only 24-case and 1,000-row comparisons produced identical complete evaluations. Initial 1k timing 64.400s→53.109s (17.5% less; sequential local benchmark, not full-scale guarantee); no acceptance coverage gain claimed. 598 unit tests, Ruff, strict mypy and diff checks pass. See `docs/SCRUM_101_MATCHER_WORK_REUSE_2026-08-28.md`. No push, rule activation, DB or graph writes; next requires evidence-backed recovery and scoped bodywork review.

## 2026-08-28 — SCRUM-101 integration and same-record validation

- Combined latest developer head `23a2ca2`, local matcher/provenance safeguards and separate calibration fixes in `NorthStar-SCRUM-101-integrated` on `feature/SCRUM-101-integrated-matcher-validation`. Fixed partial-name acceptance, raw-model precedence, narrow chassis suffix comparison, fuel alignment consumption/sealing and reviewed-fuel token refresh; versioned normalization output as v6. Same-input 20k comparison: resolved 1,515→1,510; provisional 1,506→1,551; review 15,668→15,519; hard conflicts 1,198→1,307. Prepared 24 private changed-resolved cases for domain review (4 gained, 9 lost, 11 changed KType); no correctness uplift claimed. 609 tests, 205 golden cases, Ruff and strict mypy pass. See integrated `docs/SCRUM_101_INTEGRATION_2026-08-28.md`. No commit, push, source DB mutation, rule activation or graph write; next adjudicate changes and address remaining evidence gaps before full scale.

## 2026-08-26 — High-margin calibration batch sampled

- Added `--min-margin`/`--max-margin` support to `scripts/sample_margin_calibration_set.py` and fixed the boundary case so rounded `0.25-0.30` items cannot leak into a `0.30+` high-margin sample. Created local review batch `margin-calibration-high-20260826` with 200 pending items: 100 from `0.30-0.40` and 100 from `0.40-1.00`, using the same pinned TS/TecDoc/rule versions. Exported the compact review file to `outputs/margin-calibration-high-20260826-review-list.json`; focused tests and Ruff pass. Next step: adjudicate those 200, then rerun `scripts/fit_margin_threshold.py` with `data/margin-calibration/band-weights-high-20260826.json`.
