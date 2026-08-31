# Last Context

Keep the latest 10 task entries only.

## 2026-08-30 — Controlled mixed-fuel/engine-set activation held at 20k gate

- Implemented set-valued TecDoc mixed-fuel persistence/scoring and full KType engine-set loading, plus a PostgreSQL-only complete-catalog rebuild mode. Rebuilt immutable local v6 catalog: 72,570 KTypes, 57,613 graph-safe, 14,957 candidate-only, 1,805 mixed-fuel promotions, zero Neo4j writes. Volvo activated 0/47 unreviewed bodywork proposals; Golf activated zero redundant/unsupported scoring rules. Same pinned 20k: resolved 2,284→2,346, provisional 2,218→1,883, review 13,788→14,068, hard conflicts 1,597→1,590; 532 changed rows, 22 identity changes, 160 resolved→review. Activation is held before the unscored holdout pending independent review. Ruff, targeted mypy, compilation, focused PostgreSQL/Neo4j integrations and 813 effective tests pass; one unrelated broad-mypy baseline error remains in `scripts/generate_golden_corpus.py`. Added evidence comments to SCRUM-170/173/174/175 without status changes. See `docs/TS_TECDOC_ACTIVATION_2026-08-30.md`. No push, decision persistence, alias attachment or graph mutation.

## 2026-08-30 — PR #32 green and Jira acceptance audit

- Replaced the stale `normalization-pipeline-v5` integration assertion with the canonical pipeline-version constant and pushed commit `f88d5b1` to PR #32. Local branch and synthetic-merge validation passed compilation, Ruff, mypy for 113 source files, all 205 golden cases, and all 813 tests; GitHub CI run 33306864077 passed both jobs. Audited SCRUM-164–175 and moved only directly worked SCRUM-172–175 to In Progress because the Jira workflow has no In Review state, adding ticket-specific evidence and remaining-risk comments. SCRUM-164–171 were left unchanged. See `docs/SCRUM_164_175_RECOVERY_STATUS_2026-08-30.md`. No rule activation, match-decision persistence, alias attachment, Neo4j write, PR merge, or Jira Done transition occurred.

## 2026-08-28 — PR #32 published

- Committed the integrated matcher, independent approval evidence tooling, mixed-fuel evidence model, full-source audit, tests and gate documentation as `b7b267e`, pushed `feature/SCRUM-101-integrated-matcher-validation`, and opened [PR #32](https://github.com/kastriotrama/northstar/pull/32) targeting `develop`. Local validation passed: 727 unit tests, focused evidence tests, Ruff, strict mypy, compile and diff checks. GitHub checks are running (`images` in progress, `backend` queued). No catalog rebuild, rule activation, decision persistence, alias attachment or Neo4j mutation was performed.

## 2026-08-31 — Frozen mixed-fuel candidate validated locally

- Completed all four requested gates. Reviewed and checksum-pinned all 532 v5→v6 development changes: 222 stable-identity gains, 277 conservative downgrades, 11 false fuel-conflict removals, and 22 rejected candidate identity changes. Added exact Peugeot HNSU source-model repair rules; the final v6 control is 2,492 resolved, 1,990 provisional, 13,819 review-required, 1,586 hard conflicts, 112 policy exclusions and one normalization review out of 20,000. The 11,629-row / 11,107-group frozen holdout passed: zero new hard conflicts, zero changed resolved identities, zero unsafe resolution gains and zero resolved conflict reasons. Final pins are recorded in `ingestion/release_manifests/ts_tecdoc_matcher_candidate_v1_20260831.json`; implementation commits are `d1ed4b0` and `823a830`. 763 tests, Ruff, strict mypy, compile and read-only PostgreSQL integration pass. Match decisions, aliases, Neo4j, production activation and pushes remain untouched.

## 2026-08-31 — Added exhaustive blocker pattern drilldown

- Added persisted, plate-free pattern inventory members and a paginated local review drilldown so stakeholders can inspect every vehicle in a grouped blocker pattern, including restricted local plate evidence and source record IDs. The API and review UI now expose exhaustive pattern coverage with per-pattern vehicle pages; the full audit and member backfill remain active from the 400,000-row checkpoint. Focused tests, Ruff, strict mypy and frontend syntax checks pass. No push, match-decision persistence, alias activation or Neo4j mutation was performed.

## 2026-08-31 — Defaulted pattern review to a compact top-10 view

- The blocker-pattern table now shows the ten highest-occurrence patterns first, with explicit Top 25, Top 50, and All patterns choices. The complete inventory and per-pattern vehicle drilldown remain available. Integration tests, Ruff and JavaScript syntax checks pass; no matcher data or graph state changed.

## 2026-08-31 — Added general domain decision summary

- Added a compact review-screen summary for the three cross-vehicle decisions that drive the merge: TS bodywork vocabulary, mixed-fuel representation, and `is_4wd=0` drive ambiguity. Each card shows one plain-language example and links to grouped patterns; no plate-level data is shown in the summary. Integration tests, Ruff and JavaScript syntax checks pass; no matcher data or graph state changed.

## 2026-08-31 — Expanded hard technical conflict explanations

- Added a hard-conflict-only technical breakdown in the pattern inspector. It translates conflicting fields into plain-language causes and required independent evidence for power, displacement, fuel, engine, drive, year, and bodywork, while keeping plate-level details behind the existing member drilldown. Integration tests, Ruff and JavaScript syntax checks pass; no matcher data or graph state changed.

## 2026-08-31 — Added exact hard-conflict field comparisons

- Added a plate-free technical-evidence endpoint and inspector section that compares representative TS values with actual TecDoc candidate values per conflicting field, including KType references. This makes the review decision actionable instead of reporting only a generic mismatch. Focused tests, Ruff, strict mypy and JavaScript syntax checks pass; no matcher data or graph state changed.

## 2026-08-31 — PR #32 deployed to Hetzner

- Merged PR #32 into `develop` as `b1a601b` after green branch and synthetic-merge validation, then deployed through the existing Hetzner workflow. Production runs the exact technical-conflict review UI/API and all PostgreSQL, Neo4j, Redis, Elasticsearch, and API health checks pass; the restricted 1,000-vehicle showcase is present. Production retains its prior data volumes: 89,545 raw TS rows and no full-audit match runs or pattern inventory, so the local 6.5M audit state was not copied and no plate/VIN data was transferred.
