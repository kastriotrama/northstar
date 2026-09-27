# Last Context

Keep the latest 10 task entries only.

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

## 2026-09-26 — Vehicles tab moved onto NorthStar vehicles (`core.vehicles`)

- API: new `vehicles` feature under `/v1/vehicles` (search, facets, field catalog, detail by
  `NOR-` ID with per-value source, losing values, plate history, links, rules). The TS
  screen's endpoints moved to `/v1/ts-records`. Matching looks a car up by `vehicle_id`
  and runs on its merged values over its TS derivation (`overlaid_fields` says which came
  from AIS/review/rule); the summary evaluates `core.vehicles`. Text search also finds a
  car by a previous plate and by a plate typed with a space.
- Web: Vehicles tab with NOR ID column, registry-status filter/label, source badges,
  plate history; TS screen on the new URLs.
- Also fixed: TS backfill stored tyres as a Python dict repr (regression test); mypy/ruff
  errors in the vehicle-core commit; graph test now excludes the non-graph `NOR` prefix.
  `migrate-vehicle-core` runs on every deploy; design in `docs/vehicle-core-design.md`.
- Validation: 1451 backend tests (7 new integration, 24 new/updated unit), ruff, strict
  mypy, 46 web tests, prod build; clicked through list, record panel and TS screen against
  a seeded local API. `pages.integration.spec` needs the full live copy to pass.
- Risk / next: free-text search scans the full table (OR with make/model substrings);
  run the live-copy sequence (backfill → TSC rules → AIS → rules) and check counts.

## 2026-09-26 — Overview: updating TS data from the AIS file (no AIS table)

- Added `docs/AIS_TS_UPDATE_OVERVIEW.md`, which lists exactly what would change:
  - 778,511 existing records get newer registry values, updated in place with the previous values kept in the record;
  - new values: engine code 5.96M, model year, weights, length;
  - 660,840 new passenger cars.
- Rule candidates measured (at least 5 cars and 95% agreement; unseen-car test):
  - engine code: R1–R3, 77k rules, 87% coverage, about 97% correct on unseen cars;
  - model year, max weight and length rules;
  - make + group code rules that complete new cars' TS-only fields.
- Risk / next: needs decisions A–E. The rule store needs multi-field keys and its migration on live.

## 2026-09-26 — AIS XML compared field by field with our TS vehicle data

- Added `docs/AIS_VIN_EXPORT_FIELD_ANALYSIS.md` (6,403,668 passenger cars in both) and corrected the plan v2 where the comparison contradicted it (month, EV kW, name).
- Valuable:
  - 660,840 active passenger cars we don't have (our TS ends early Dec 2023);
  - engine code for 5.88M cars (98.1% determined by variant/version, so a lookup table is possible);
  - changes since 2023: 672,563 deregistered, 12,650 converted, 5,983 plate moves;
  - new fields: weights, length, model year.
- No value: fuel, gearbox, body, group, month, tyre, name (all copies of TS).
- Risk / next: provider questions on the deletion flags. The sandbox schema `sandbox_step_xml` uses 6.9 GB of local disk (16 GB free).

## 2026-09-26 — AIS enrichment plan v2, measured on the full live copy

- Wrote `docs/AIS_VIN_EXPORT_ENRICHMENT_PLAN.md` (v2). Each plan step was simulated with the real matcher on 30,000 random passenger cars (prod-v2 catalog), with the AIS engine code as an accuracy proxy.
- Findings: today 16.0% resolve. A naive XML import is net 0. Tolerant engine compare +1.5 points, hybrid kW +1.5, body fallback +9.7 (accuracy equal to today), and engine-confirmed candidate-only k-types up to +14.9. The XML name equals the TS brand text, so it adds no model information.
- Risk / next: the accuracy check is a proxy, so build a reference set (step 0). Analysis scripts are in the session scratchpad, not the repo; step 0 turns them into a CLI.
