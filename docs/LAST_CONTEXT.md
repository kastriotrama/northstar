# Last Context

Keep the latest 10 task entries only.

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

## 2026-08-17 — Detached full VD-AI passenger import

- Verified the authoritative VD-AI PostgreSQL source contains 10,455,988 vehicles and exactly 6,515,471 passenger-eligible rows. The retained-raw V3.2.3 replay checkpointed 14 parts / 350,000 rows (282,746 resolved, 67,088 provisional, 166 review-required, 0 failed), then its incomplete part 15 was removed and restarted safely as macOS launchd job `com.northstar.vdai-full-import`. The detached job runs with PPID 1, resumed part 15 with 25,000 staged rows, and logs to `/tmp/northstar-vdai-full-import.log`.

## 2026-08-17 — Local datastore reconciliation and full-source restore gate

- Applied the SCRUM-171 migrations locally and reconciled PostgreSQL with Neo4j. The checkpoint ledger proves 6,515,471 passenger rows were processed in 267 parts, but pruning left 738,960 raw rows / 568,469 plates; Neo4j contains 55,808 KTypes, all `Provisional`, with zero TS aliases, while the new decision tables are empty. The VD-AI database and `REMOTE_DATABASE_URL` are unavailable locally; the only file is an older 6,300,739-line snapshot, and available disk is 30 GB. No partial substitute import, KType promotion, or alias write was performed.

## 2026-08-17 — SCRUM-170 controlled KType promotion

- Added dry-run, controlled-cohort and production graph modes that consume only current resolved SCRUM-171 decision heads. Preflight requires one TecDoc KType target, rejects conflicting or multi-target TS aliases, then atomically removes `Provisional` and attaches a collision-safe evidence alias with the immutable decision ID. Replays are idempotent and a PostgreSQL-to-Neo4j reconciliation query reports missing or divergent assertions. Live Neo4j/PostgreSQL integration tests and Ruff pass.

## 2026-08-17 — SCRUM-171 immutable match decision lifecycle

- Added an explicit dry-run/persist boundary for TS-to-KType match decisions, one current decision head per stable source identity/version, and append-only supersession evidence while retaining deterministic immutable decision rows. Dry runs validate source provenance without writes; persisted retries are idempotent and conflicting supersession histories stop safely. PostgreSQL integration and migration contract tests pass; this layer does not promote KTypes or attach Neo4j aliases.
