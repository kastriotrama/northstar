# Last Context

Keep the latest 10 task entries only.

## 2026-08-25 — Margin-calibration verdicts applied

- Applied the approved 200-case `margin-calibration-20260824` verdict JSON to local `core.review_queue` as terminal resolved calibration verdicts: 103 accept, 67 reject, 30 unsure, 0 pending, and 0 unlabelled. Ran `scripts/fit_margin_threshold.py` with `data/margin-calibration/band-weights-20260824.json`; no threshold met the 95% precision lower-bound plus minimum effective sample gate. The clean high-margin region needs more labels before changing matching policy; no confidence route, promotion policy, match decisions, or aliases were changed.

## 2026-08-25 — Margin-calibration review workflow ready

- Added a scoped review-queue workflow for the 200 `margin-calibration-20260824` cases: direct batch filtering, sanitized TS evidence, top/runner-up KType evidence, and auditable `accept`/`reject`/`unsure` verdicts matching the threshold fitter contract. Added a durable `(source_batch_id, status, updated_at, id)` queue index and a calibration fast path; applied the idempotent migration locally. The live port-8001 API returns all 200 pending cases, 9 focused tests, Ruff, strict mypy and diff checks pass, and no human verdicts or matching decisions were invented.

## 2026-08-25 — Non-hard context routing gate corrected

- Ran the pending deterministic cohort and found that matcher-level drive/body conflicts could still resolve because the confidence router gated only hard-conflict fields. Added an explicit `context_conflict_requires_review` router gate and sanitized per-field soft-conflict reasons. The accepted fresh 10,000-row run (`13a3fbd0-06c8-430e-88a7-10d76fa5e3af`) produced 709 resolved, 79 provisional, 7,405 review-required, 1,751 hard conflicts, 56 policy exclusions and 0 failed; 1,543 stayed review-only through the new gate, with 2,605 bodywork and 48 drive top-candidate conflicts. All 471 unit tests, Ruff, strict mypy and diff checks pass. The existing 200-item margin-calibration adjudication remains the next step; no decisions or aliases were persisted.

## 2026-08-24 — TS-to-TecDoc matching continuation handoff

- Added `docs/TS_TECDOC_MATCHING_HANDOFF_2026-08-24.md` with the completed 6,515,471-row audit, implemented reason/model/drive/body work, validation evidence, exact pending 10,000-row cohort command and queries, acceptance gates, remaining work, safety boundaries, and relevant files. Current branch/HEAD are recorded; `docs/PHASE_1_PLAN.md` remains unrelated untracked user work and must not be staged implicitly.

## 2026-08-21 — Alternative model evidence and drive/body matching context

- Extended exact-manufacturer, token-bounded, unique-longest TecDoc model recovery from Brand to Variant, Version, model number, type text and EEG type approval evidence. Added canonical TecDoc drive/body fields to KType candidates and TS match queries with conservative context bonuses and review-only conflict penalties; disagreements cannot bypass routing gates or become hard conflicts automatically. Ruff, strict mypy, 57 focused unit tests and diff checks pass. The deterministic 10,000-row remote comparison could not start because workspace approval credits were exhausted; rerun that cohort and PostgreSQL integration validation before any full audit or promotion.

## 2026-08-21 — Dry-run reason profiling and safe Brand model recovery

- Added operation-scoped `core.match_run_reason_counts`, atomic per-checkpoint reason aggregation, and sanitized evaluator reasons without retaining plates, VINs or raw payloads. Added exact-manufacturer, token-bounded, unique-longest TecDoc model recovery from Brand text when TS model is absent. A deterministic 10,000-row dry run produced 38 resolved, 141 provisional, 9,366 review-required, 399 hard conflicts, 56 policy exclusions and 0 failed; 132 models were recovered, yielding 28 resolved, 33 still review-required and 71 explicit technical conflicts rather than unsafe promotion. Ruff, strict mypy, 51 focused tests and the PostgreSQL integration test pass. Next: profile a broader stratified cohort, then add variant/type evidence and drive/body filtering under separately validated gates.

## 2026-08-20 — Full-audit review backlog diagnosis

- Profiled the authoritative 6,515,471-row passenger cohort and restored TecDoc 0326 graph after the completed count-only audit. TS has model on 4,126,500 rows; 2,388,971 lack model, but 2,109,530 of those retain variant/version/model number/type text/type-approval evidence. Core evidence is broadly populated (year 6,515,129; fuel 6,512,991; power 6,181,921; displacement 4,843,011; drive 6,512,991; body code 6,484,230). Only 2,408 manufacturer/model catalog families have one KType, but manufacturer/model plus year range/fuel/displacement/power yields 45,103 unique TecDoc technical signatures. Highest-impact next work is a persisted reason/evidence profiler, safe model recovery from brand/variant/type evidence, hierarchical technical filtering, drive/body integration, and calibrated numeric tolerances; do not promote review/provisional rows before rerun validation.

## 2026-08-20 — Full passenger TS-to-TecDoc dry-run completed

- Completed the version-pinned, write-free audit of all 6,515,471 passenger TS rows against 55,808 TecDoc 0326 KTypes: 8,104 resolved, 80,290 provisional, 6,041,725 review-required, 295,669 hard conflicts, 2,336 normalization reviews, 86,792 policy exclusions, 555 unmatched, and 0 failed. PostgreSQL status is `completed` and terminal accounting equals the source total exactly. Added guarded handling for invalid normalized model text and skipped futile all-catalog scoring for global manufacturer scope in this count-only audit; Ruff, strict mypy, and 39 focused tests pass. No match decisions or Neo4j aliases were persisted; next step is reason/evidence profiling before changing matching policy.

## 2026-08-18 — Reproducible full passenger import command

- Added `northstar-ingest import-remote-passenger` with the shared V3.2.3 prefix, exact 6,515,471-row source-count gate, 25,000-row checkpoints, raw retention, and explicit singleton stale-part recovery. Added `REMOTE_DATABASE_URL` configuration, team runbook and focused CLI/recovery tests. This replaces the temporary local runner contract so every developer can resume the same plate-ordered VD-AI import safely.

## 2026-08-18 — TS-to-TecDoc validation work breakdown

- Expanded the validation runbook with the SCRUM-171/SCRUM-170 dependency chain and implemented both local and remote write-free runners. The authoritative remote TS source confirms 6,515,471 passenger rows, and the new plate-keyed path normalizes/matches bounded batches while retaining only monotonic checkpoints. A 100-row smoke run stopped before any checkpoint because the live Neo4j database has zero nodes; all local PostgreSQL TecDoc staging tables are also empty and no pinned 0326 source files are present. Restoring the 55,808-KType catalog is now the only blocker; no Jira, decisions, aliases or production data changed.
