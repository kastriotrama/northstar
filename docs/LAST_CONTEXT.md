# Last Context

Keep the latest 10 task entries only.

## 2026-09-29 — Matching tab evaluates a seeded random sample, not the lowest NOR IDs

- A 200-car Matching run gave 20.5% one KType: `vehicle_population` took the first 200 by NOR ID, and
  those lack a model five times as often (51% vs 11%). It now orders by `md5(SAMPLE_SEED || vehicle_id)`,
  the impact report's seed (`SAMPLE_SEED` in the repository; the report's `--seed` default uses it), so
  the same filter picks the same cars. Page and API wording now say "random sample".
- Same filter, 200 cars: one KType 20.5% -> 42.5%, resolved 26.0% -> 50.5%. Random 2,000 registered
  passenger cars via the impact report: resolved 54.9% (30k reference 55.1%).
- Validation: new integration test (seed chosen so seeded order differs from NOR ID order); backend
  1677 passed (CI env), ruff, mypy; web build; web tests 48/49 — `pages.integration.spec` "TS data
  resolves a field" is flaky against the live copy and fails on the unchanged commit too.
- Risk / next: the sort reads the whole filtered set once per run (seconds at 7M rows); fix the flaky
  TS data spec (fixed settle rounds against slow live queries).

## 2026-09-29 — Integration tests refuse to write outside ENVIRONMENT=test; portable launch.json

- `tests/integration/conftest.py` skips every integration test unless `ENVIRONMENT=test`; tests marked
  `read_only_database` (the frozen-holdout loader, read-only transaction) still run. With a local `.env`
  pointing at the live copy, a plain `pytest` no longer writes test batches into it.
- `.claude/launch.json` `api` no longer hardcodes another machine's paths (`cwd: apps/backend`, its `.venv`).
- Validation: plain `pytest` on the live copy 1500 passed / 176 skipped, nothing written (no `nstest_`
  databases, no new catalog batches); CI-style run on disposable datastores 1676 passed; ruff, mypy clean;
  API started from the launch config and reported healthy.
- Risk / next: run integration tests with the CI env (see `.github/workflows/ci.yml`) against disposable
  datastores; the live copy still uses the compose default password.

## 2026-09-28 — Model family for cars the registry named only by make (local, commits not pushed)

- Seven `MOD-*` rule families fill `core.vehicles.model_family` (99.77–99.99% on a holdout); a model guard
  refuses or takes back fills the car's own model word contradicts, and number-keyed rules only fill cars of
  their learned era. The matcher reads model words, names and family words (`recover_model_from_evidence`
  readings); rule-inferred models yield to the car's text. Report: `docs/MODEL_FAMILY_FILLING_2026-09-27.md`.
- Final run: check took back 6,524 out-of-era fills (1950s MB 170 S → SLK, Citroën 7 CV → Berlingo); the
  re-apply made no new fills. Resolved 49.5% → 55.1% (30k), 49.0% → 54.6% (50k); includes the merged
  matcher change `65f6eb6` (+247/−77 on the 30k). The era query was re-planned (never finished → 32 s).
- Validation: 1,514 unit tests + model-rule integration tests, ruff, mypy; lost/moved checked car by car.
- Risk / next: 32 cars lost to the one-year tolerance and 45 Subarus to revision families (raise with the
  matcher author); rebuild the 50k sample dump with model fills; pushing to PR #55 needs confirmation.

## 2026-09-27 — Status check: matching lookup and summary endpoints (read-only)

- `GET /v1/vehicles/matching/lookup` and `POST /v1/vehicles/matching/summary` (+ poll/cancel) are in PR #55
  (CI green, not reviewed; needs #54 first). 35 unit tests pass.
- Found: unpinned, both endpoints use the newest catalog batch, which on the local DB is one of 100 1–3 KType
  test batches that integration runs left behind; every car comes back `none`. Pinned to prod-v2 they work
  (lookup 0.13 s; summary 300 cars in 19.6 s, ~0.065 s/car).
- Next: pin/validate the catalog batch, seeded sample instead of first-N by NOR ID, matcher reload on rule
  changes, persisted jobs, then persisted match decisions per vehicle and a real `/v1/resolve`.

## 2026-09-27 — 50k-car sample database for testing (local, not in git)

- `outputs/sample-db/northstar-sample-50k-2026-09-27.dump` (85 MB, pg_restore custom format; the folder has
  its own `.gitignore`, the dump holds licensed TecDoc data and real plates/VINs): 50,000 registered passenger
  cars (seed `northstar-test-dump-v1`, the 30,000 impact-sample cars excluded) with their TS records,
  normalization, identifiers, links, ledger, facts, rule applications and review items; all rules whole; the
  pinned TecDoc batch. Built in local database `northstar_sample_50k`; README and build script beside the dump.
- Validation: restored into a scratch database with `--exit-on-error` (counts match, dropped after); API
  search (50,000) and matching lookup work against it; impact report on all 50k: 47.9% resolved (48.2% on
  the tuning sample).
- Risk / next: pipeline v10 like the source (no `2wd`); no Neo4j graph or match chunks; share privately only.

## 2026-09-27 — Body compatibility rulings in the matcher (local)

- Matcher reads registry-to-TecDoc body rulings (vocabulary `bodywork`, `core.tecdoc_resolution_rules`,
  relation `compatible`) like drive: a broader registry body scores neutral with a small penalty, never
  a conflict; an exact body still wins. Wired into the Vehicles-tab matcher and `match-ts-tecdoc`.
- Local rulings written via `TecDocReviewRepository.insert_compatible_resolution`, reviewed_by
  `claude-proposal-2026-09-27 (local; awaiting data-owner review)`: `covered_body` -> sedan, hatchback,
  estate, coupe, suv, MPV; `multi_purpose_vehicle` -> bus, van. Included in
  `outputs/rules-2026-09-27/rules_export.json` (32 TecDoc resolution rules).
- Same 30,000 cars: 47.5% -> 48.2% (+220, 0 lost, 0 moved). A first version without the penalty lost 44
  MPVs whose exact MPV KType tied with a van sibling.
- Drive fill not done: every local TS result is pipeline v10; v11's `2wd` rule never ran (2.34M cars).
  Needs `renormalize_all` (7,255,433 rows, ~8 h) and a vehicle refresh; host has ~9.5 GB free vs a 33 GB
  results table, so it needs disk first.
- Validation: 1386 tests (unit + vehicles API + new bodywork alignment integration), ruff, mypy.

## 2026-09-27 — Hybrid power and engine-confirmed candidate-only KTypes (local)

- Hybrid power: TS gives combustion kW, TecDoc system kW. When car or KType is a hybrid and the car's kW is
  lower, power is `power_kw_hybrid_unverified` (small penalty, no conflict); higher still conflicts. The
  catalog has no engine power (local `staging.tecdoc_engine` is empty), so this is the matcher-side fix.
- Candidate-only KTypes resolve when the car's own engine code (registry/AIS, not a fingerprint inference)
  exactly matches one of the KType's engines (`candidate_only_engine_confirmed`).
- Same 30,000-car sample, baseline -> final: resolved 23.9% -> 47.5% (+7,076, -6, 0 moved); engine
  confirmation alone +18.6 pts. Engine agreement is consistency only here: no independent accuracy yet.
- Validation: 1371 unit tests, ruff, mypy.
- Risk / next: enable on live only after a reviewed reference set; stakeholder decision 4 (is engine
  confirmation enough?) is still open.

## 2026-09-27 — Match impact report and tolerant engine-code comparison (local)

- New `scripts/match_impact_report.py` (docs/match-impact-report.md): seeded, catalog-pinned sample of
  `core.vehicles`, real matcher, terminals, engine agreement, optional reviewed reference set, car-by-car compare.
- Baseline, 30,000 registered passenger cars, catalog prod-v2: resolved 23.9% (plan v2 said 16.0% before
  body corrections and AIS engine codes). Peugeot 0%.
- Matcher: engine codes match on TecDoc bracket parts (`BHZ` = `BHZ (DV6FC)`); a bare family matches its
  variants with half the bonus (`K9K` vs `K9K 276`), but two variants (`D4F-742` / `D4F 740`) still conflict;
  a code no KType carries is `engine_code_unverified` and routes to provisional, never resolved.
- Result on the same sample: resolved 25.6% (+1.7 pts, +508, 0 lost, 0 moved); hard conflicts -6.4 pts;
  Peugeot 0% -> 22.4%. A first version also matched variants and lost 52 Renault cars; narrowed.
- Validation: 1362 unit tests, ruff, mypy; integration suite passes except two failures that also fail on
  the prior commit (bundle import fixture, normalization review repository duplicate key).
- Next: reference set (needs a person), hybrid/EV power, candidate-only KTypes confirmed by engine.

## 2026-09-27 — Review corrections no longer drop the registry value (local)

- `vehicle_core_ts.process_ts_page` folded reviews into the TS values (`{**ts, **reviews}`), so a corrected field
  never kept its registry value and retiring the review could not restore it (131,102 vehicles repaired by hand).
  `merge()` now takes ordered layers; the TS import merges TS values, then reviews.
- Checked: the Vehicles-tab matcher reads merged `core.vehicles` values (corrected body, AIS engine code).
- Validation: regression test; 1339 unit tests, 23 vehicle-core integration tests, ruff, mypy.
- Rules for live exported (untracked): `outputs/rules-2026-09-27/rules_export.json` (5,223 TS rules, 5,055 body)
  and `retire_on_live.json` (38 rules incl. Volvo `31614f07`; the importer never retires).
- Next: matching harness + reference set, engine-code tolerance. Nothing pushed.

## 2026-09-27 — KType matching re-checked on corrected cars (1,190 stratified + 20,000 random)

- Each car was matched twice with the Vehicles tab's matcher (catalog `tecdoc-0326-canonical-full-prod-v2-20260914`),
  once with the old registry body type and once with the corrected one. Read-only.
- Random 20,000 corrected passenger cars:
  - exactly one KType 8.8% → 36.8%;
  - no compatible KType 74.2% → 27.3%;
  - the best KType's body matches the car 3.9% → 98.7%.
- Old single matches to another model: 359 of 1,668 (mostly XC60 → V60 I). Now 97 of 5,630, nearly all BMW GT /
  Gran Coupé naming.
- Worse on 661, mostly one → several:
  - Golf / Golf Sportsvan both hatchback in TecDoc;
  - XC60 / Kodiaq / Tiguan share engines across SUV KTypes.
- 43 went one → none:
  - XC60s whose old match was the wrong model (V60);
  - Scénic III filed under "Megane".
- The first 20K attempt crashed a parallel Postgres worker. The Docker VM disk then went read-only (host had 2.9 GB free).
  After the Docker restart, recovery was clean and counts were unchanged. Scratch data (~575 MB) was deleted.
- Next:
  - model alias MEGANE SCENIC → Scénic;
  - bus ≈ MPV alignment for M1 people carriers;
  - XC60 169 kW KTypes missing from the catalog;
  - consider moving resolution rules onto `core.vehicles` as a separate story.
