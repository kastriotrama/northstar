# Last Context

Keep the latest 10 task entries only.

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

## 2026-08-31 — Restricted 500k cohort imported to production

- Created a checksum-pinned 103 MB private bundle for exactly VD-AI parts 001–020: 500,000 distinct plates, the 169,190-row v6 TecDoc candidate batch, 169,190 identity rows, 70,671 KType-engine relationships, and the pinned active rule version. Backed up production, imported the bundle transactionally with zero ID/prefix collisions, and reconciled 72,570 KTypes; Neo4j and the 1,000-car showcase were unchanged. Started checkpointed production match run `d2610431-29c3-43a0-b3c0-71d8637a6daf` for exactly 500,000 rows in an isolated container. The run is healthy and CPU-bound; the live UI will update every 25,000 rows.

## 2026-08-31 — Located the existing full database snapshot

- Located and validated the earlier full PostgreSQL archive at `NorthStar-SCRUM-95-98/outputs/snapshots/northstar-ts-tecdoc-v323-6515471/northstar-postgres-ts-v323-6515471.dump`. The 991,886,805-byte custom-format dump includes the 6,515,471-row TS snapshot contract plus normalization, review, TecDoc candidate/relationship, and match-decision tables; SHA-256 is `eb9ad39fe5184b7457ffe833fb5bd0e23d7a195891600ce276f036d564188a86`. Archive listing validation passed and permissions were restricted to the owner. The mistakenly started replacement dump was stopped and its incomplete artifact removed; the source database was unchanged.

## 2026-08-31 — Local full matcher stopped cleanly

- Stopped only local matcher process `84986` with `SIGTERM`; the production 500k matcher container remains active. The local resumable run retains checkpoint 51 at 1,275,000 / 6,515,471 rows (19.569%), with zero failures. Its database status intentionally remains `running` so the same pinned operation can resume from the next 25,000-row batch without reprocessing committed work.
