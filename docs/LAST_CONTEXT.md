# Last Context

Keep the latest 10 task entries only.

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

## 2026-09-27 — Body type corrections applied to `core.vehicles` (local), audited and fixed

- Retired the over-broad Volvo rule `31614f07` first.
- Applied the proposal through the rule service (same validation and runner as the TS data screen). Each batch writes
  the TS projection, the ledger and the linked NorthStar vehicles together.
- AIS-only vehicles got the same rules as review observations with the same rule id (416,338 vehicles).
- An audit after applying checked registry text, length, sibling registrations and scope. It found 37 wrong rules:
  - variant codes that other models share (Antara, Mazda6, Doblò, Maserati Coupé, MINI Clubman, Cadillac SRX);
  - "D-4D" read as 4-door;
  - Rapido S80 motorhomes as S-Class sedans;
  - Fiat 127 "Combi";
  - classic cars TecDoc lists wrongly (MG, Firebird, Eldorado …).
- Those were retired and replaced by 79 narrow rules that follow the registry text (2,618 rows).
- Result: 5,048 active rules. Carrying a correction: 2,518,331 TS records and 2,934,027 NorthStar vehicles
  (2,517,689 TS + 416,338 AIS-only).
- Passenger changes: suv 10,738 → 1,691,749, estate 3,371,227 → 2,153,644, MPV 1,043,943 → 314,421,
  empty 160,457 → 21,798.
- Data repair:
  - 214 vehicles emptied by the Volvo retirement were refilled from TS.
  - 131,102 corrected vehicles now keep their registry value behind the review, so retiring a rule restores it.
  - Cause: `vehicle_core_ts.process_ts_page` merges `{**observations, **review_observations}`, which drops the TS
    value. Needs a code fix and a regression test (offered as a separate task).
- Validation:
  - 0 orphan review markers.
  - TS-linked passenger vehicles agree with the TS projection except 2,126 newer AIS values (unchanged) and 3 vehicles
    linked to a second TS record.
  - 17 motorhomes carry suv/MPV.
  - Report: `docs/BODYWORK_CORRECTIONS_2026-09-26.md`, "Applied" section.
- Risk / next:
  - local DB only;
  - AIS-only corrections were a one-time pass;
  - gray zone applied at TecDoc labels (flagged in rule notes);
  - opt-in crossovers not applied;
  - classic cars without a body word keep TecDoc's label;
  - no code changed, nothing committed.

## 2026-09-26 — Vehicles tab: plate search from 36.6 s to 0.06 s; bundle fixture excluded

- Free-text search is looked up before the query runs: identifier history by exact value, manufacturer and
  model-family names by a skip scan of their indexes. The query carries the vehicles and names found as values,
  so the planner uses indexes instead of walking 7.2M rows.
- New `text_pattern_ops` indexes on plate and VIN (the collation keeps a default index from answering
  `LIKE 'ABC%'`). They replace the default indexes; migration applied locally (9 s).
- Measured: plate, VIN, previous plate or NOR ID 0.06 s (was 36.6 s); "volvo v70" 1.9 s; a facet with plate text
  0.06 s. In the browser, `LGF109` returns its one car in 0.37 s.
- The normalization bundle's fixture car (`TEST001`, batch `normalization-bundle-fixture-v1`) was the first row of
  the passenger list. Fixture batches are now test records; it was refreshed to `test_record` (2,024 in total).
- Validation: full backend suite on a throwaway database (1466 passed, 29 skipped), ruff, mypy.
- Risk / next: the first load of the tab still takes ~7 s (ten full-table facet counts); cached counts are next.
  All changes since `d979254` are uncommitted.

## 2026-09-26 — First full vehicle-core run on the local copy of live

- Applied the cloud session's API/UI patch (`d979254`) on top of `82aa4c8`. Then ran every step:
  - TS backfill (~80 min);
  - test records excluded (2,023);
  - completion rules (227,852);
  - AIS import (134 min: 660,846 new vehicles, 674,042 deregistered, 11,955 type changes, 2,562 plates closed);
  - enrichment rules (148,260 learned; 145,252 fills incl. 95,382 engine codes).
- Result: 7,193,254 vehicles. Registered passenger cars: engine code 90.8%, model year 99.8%, kerb weight 99.1%.
- Code fixes from the run:
  - synthetic test records (`TEST-` plates, `TEST/` brands, quarantined) are never minted;
  - `apply_rules` analyzes the rules table first (a stale plan cost 10 min);
  - the AIS import no longer re-normalizes every car with a second fuel.
- Validation: ruff, strict mypy, backend suite (throwaway databases), web tests and build.
- Risk / next:
  - About 15 GB on disk; live (~14 GB free) needs more disk before this runs.
  - The Vehicles tab fires about a dozen full-table counts per load, which is slow at 7M rows; needs cached counts and an identifier fast path.
  - Matcher engine-code tolerance (plan step 1).
  - The changes since the patch are uncommitted.

## 2026-09-26 — Body type corrections: rules for 2.34M passenger cars (proposed, not applied)

- Checked all 6.44M passenger cars in `core.vehicle_facts` against TecDoc prod-v2, with AIS vehicle
  length as evidence. 2,342,644 cars (36%) carry a wrong or vague `bodywork_form`. Largest:
  - estate→suv 686k, MPV→suv 334k, MPV→hatchback 280k, estate→hatchback 266k;
  - the Golf alone has 100k hatchbacks registered AC; type AU/1K/CD vs AUV/1KM/CDV separates them.
- 4,401 override/fill rules in `match_resolution_rules` shape in `outputs/proposed/bodywork/`; report
  in `docs/BODYWORK_CORRECTIONS_2026-09-26.md`. Rule kinds: TecDoc single body, reviewed
  discriminators (type code/variant), registry text, national codes, TecDoc disputes (Model X,
  PV544, Saab 96…). The gray zone (126k) is flagged with alternatives; opt-in crossovers 35k.
- Validation: 283 rules compiled with `compile_predicate` and counted in the DB, all exact. Automatic
  rules agree with per-car evidence on 99.97%. 0 non-passenger rows. Nothing written to the DB.
- Risk / next:
  - retire rule `31614f07…` (over-broad Volvo 'model contains VOLVO' → suv) before applying;
  - stakeholder calls on the gray zone, crossovers and M1 people carriers;
  - 431k cars have no identifiable model, and 175k keep a vague national code;
  - the matcher needs bodywork context rules where the values now differ from TecDoc.
