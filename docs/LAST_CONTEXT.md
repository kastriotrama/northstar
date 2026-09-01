# Last Context

Keep the latest 10 task entries only.

## 2026-08-31 — Frozen mixed-fuel candidate validated locally
## 2026-08-31 — Unresolved-field explorer and rule preview

- Introduced field resolution semantics (`resolved` / `unresolved` / `missing`): a source value NorthStar cannot interpret is now distinguished from an absent one, and shown per vehicle as "unknown meaning" vs "missing". Added `GET /v1/match-review/unresolved` (ranked unresolved populations), `GET /v1/match-review/unresolved/discriminators` (which fields split a population, scored by coverage × separation × concision) and `POST /v1/match-review/rule-preview` (dry-run: matched rows, would-resolve, already-resolved, sample plates; canonical target vocabulary enforced, writes nothing). Production evidence: `is_4wd = 0` leaves `drive_type` underivable for 191,921 rows in the build (~77% of the whole fleet); `IF is_4wd=0 AND fab_code=VO THEN drive_type=fwd` previews 44,253 rows with zero conflicts. Scoring deliberately demotes `eu_category` (constant M1) and `kw` (420 values). Ruff, strict mypy and 507 unit tests pass. Next: persist previewed rules through the existing immutable translation-rule draft/activation machinery.

## 2026-08-31 — Rule expressiveness, attribute picker, and rule advice

- Recovered from a data-directory deletion that destroyed `staging.transportstyrelsen_raw` and `core.normalization_results`: re-restored the dump and rebuilt chunks to byte-identical counts (35,691 chunks / 226,529 rows). Completed the half-applied condition refactor and extended it: conditions now carry `layer` (source/normalized) and operators `equals|not_equals|starts_with|contains|gte|lte`, with OR inside a condition and AND across conditions; numeric casts are regex-guarded. Added `GET /unresolved/attributes` (all 30 population keys, 16 never shown by the ranked list, with sampling disclosed), `GET /unresolved/patterns` (prefix discovery — `MERCEDES-BENZ 204` = 926 cars over 2 spellings), and `POST /unresolved/advise` (`PatternRuleAdvisor` deterministic, `LlmRuleAdvisor` when a key is set, falling back on failure). The advisor withholds `target_value` unless OEM samples agree, and `TARGET_FIELD_PRIORS` makes identity fields outrank `vehicle_year` for drive type. Fixed a real prefix bug (full-length token prefixes were skipped, hiding chassis codes). Ruff, strict mypy and 517 unit tests pass; operators verified against SQL ground truth. Rule persistence and the pattern/expansion UI remain unbuilt.

## 2026-08-31 — Source-spread gate stops unsafe chunk extrapolation

- Added `GET /v1/match-review/chunks/{chunk_id}/field-profile`: distinct raw TS values per field across a chunk's members (5,000-row scan bound), reporting which fields vary. Varying identity fields (`brand`, `model`, `model_no`, `variant`, `type_text`) now make the heuristic adjudicator return `split_chunk` before any paid OEM lookup, and the screen marks the chunk `mixed identity`. Real production evidence for the gate: the largest chunk (1,564 rows, signature `Volvo / unknown model / 1967 / petrol / 55 kW`) holds 26 distinct `brand` strings and 10 `model_no` values — different Volvo Amazon variants collapsed because TS supplied no model. Sampling one car there would have extrapolated a wrong decision across all 1,564 rows. Ruff, strict mypy and 496 unit tests pass; endpoint verified live at 250 ms over the full chunk.

## 2026-08-31 — Vehicle-level review context on the chunk dashboard

- Added `GET /v1/match-review/chunks/{chunk_id}/members/{source_record_id}`: a field-by-field comparison of Transportstyrelsen evidence, the normalized signature and OEM evidence, flagging a conflict only when normalized and OEM values both exist and disagree (null, not false, when OEM evidence is absent). Members now carry readable labels (plate + manufacturer + model + year) instead of bare record ids, the detail pane auto-selects the first member as the "checking vehicle", and rows/picker stay in sync. Added a root redirect to `/match-review`. Ruff, strict mypy and 491 unit tests pass; verified live against the restored production batch. TecDoc is still absent from the comparison because the dump contains no TecDoc staging rows.

## 2026-08-31 — Production dump restore, real-data chunk build, readability redesign

- Restored the production NorthStar app dump (`northstar-postgres-ts-v323-6515471.dump`, 2026-08-18) into local database `app` on homebrew PostgreSQL 16: 7,254,431 raw TS rows, 741,327 normalization results, 35,467 queue items; schema matches repo migrations exactly and raw records carry `vin`. Built chunks from batch `normalization-ts-remaining-passenger-cars-20260807` (226,529 provisional/review rows → 35,691 chunks; top 1,000 chunks cover 46% of rows) and verified `/match-review` live with headless Chrome, fixing a flex-basis bug that broke the fixed-height workspace. Replaced every serif font and lifted the 7–12px type ladder to ≥10.5px across the whole normalization stylesheet. Local disk is nearly full (~600 MB free after clearing pip cache); the earlier `vehicle_db` restore (VD-AI source schema) remains broken for `swedish_vehicles`/`vehicle_platform_map`.

## 2026-08-31 — Chunk review dashboard increment 1

- Implemented leverage-first chunk review per `docs/chunk-review-dashboard.md`: deterministic technical-signature chunking (`build-match-chunks` CLI, `core.match_chunk_*` tables), an append-only `staging.oem_vin_evidence` cache keyed by provider/VIN/dataset so each paid VIN lookup is billed once forever, a heuristic adjudicator behind the `MatchAdjudicator` protocol (LLM agent adapter is increment 2), and the `/match-review` screen with a clean sans-serif design. Proposals never write decisions; approval is chunk-level and the SCRUM-171 apply step is deferred to increment 2. Ruff, strict mypy, 487 unit tests, and a live-PostgreSQL end-to-end smoke (builder idempotency, evidence immutability trigger, full API lifecycle) pass; OEM provider name/credentials remain unconfigured pending contract confirmation.

## 2026-08-21 — Alternative model evidence and drive/body matching context

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
- Completed the version-pinned, write-free audit of all 6,515,471 passenger TS rows against 55,808 TecDoc 0326 KTypes: 8,104 resolved, 80,290 provisional, 6,041,725 review-required, 295,669 hard conflicts, 2,336 normalization reviews, 86,792 policy exclusions, 555 unmatched, and 0 failed. PostgreSQL status is `completed` and terminal accounting equals the source total exactly. Added guarded handling for invalid normalized model text and skipped futile all-catalog scoring for global manufacturer scope in this count-only audit; Ruff, strict mypy, and 39 focused tests pass. No match decisions or Neo4j aliases were persisted; next step is reason/evidence profiling before changing matching policy.
