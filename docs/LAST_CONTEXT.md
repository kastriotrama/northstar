# Last Context

Keep the latest 10 task entries only.

## 2026-10-03 — Stored match results: statistics and car lists read from a table (branch feature/vehicle-match-results, uncommitted)

- New `core.vehicle_match_results` (one row per NOR ID, foreign key to `core.vehicles`) and
  `core.vehicle_match_runs`. A row holds the state (`resolved`, `several`, `one_unconfirmed`, `none`,
  `not_matchable`), the accepted KType (only when resolved), the possible KTypes with confidences, the
  separating / missing / conflicting fields, reason codes, catalog batch, matcher version and the hash of
  what the matcher was handed. A cache: overwritten on re-match; a person's choice stays in its own table.
- Read side, never the matcher: `POST /v1/vehicles/match-results/overview` and `.../cars` take the Vehicles
  filter; a person's choice counts as `chosen` / `chosen_none`, a car without a row as `not_evaluated`.
- Writer `scripts/refresh_vehicle_match_results.py`: new and changed cars by default (timestamps select, the
  input hash decides), `--sample N`, `--rebuild`, `--vehicle`; each page of 1,000 commits on its own.
  Created by `migrate-vehicle-core`; the pilot builder carries both tables (18 migration sets).
- Validation: unit 3,049 and integration 512 passed (run separately: two files named `test_tyre_reparse.py`
  collide when collected together), ruff clean, mypy clean on api + ingestion + northstar.
- Local copy `northstar_pilot_corr`: 10k sample in 445 s with 4 workers (22.5 cars/s): resolved 7,016,
  several 1,646, one unconfirmed 397, none 573, not matchable 368. A second run of the same sample: 0 matched,
  10,000 unchanged in 10 s. Overview of all 500k: 1.1 s; a state's car page: 0.02 s. Full fill started.
- Next: the web screen, refreshing a car's row when a correction or choice is saved, match state as a filter
  in the Vehicles list, overview query time once all 500k rows exist. Details: `docs/vehicle-match-results.md`.

## 2026-10-03 — Drive type for the cars the model table left open (branch feature/vehicle-corrections)

- 23,233 of the 500k cars had no real drive type after `DRV-MY`: 15,076 with no four-wheel-drive statement
  (inspection-register-only cars, mostly 2023-2026) and 8,157 marked "not four-wheel drive" whose model did
  not settle the axle. Two steps added, run in this order after `DRV-MY`:
  1. `DRV-CAR` (reviewed, `drive_variant` in `vehicle_drive_layouts.py`): the variant by power (electric: one
     motor or two), fuel, body (BMW 2 Series), registry text for cars without a model ("VOLKSWAGEN 1303 S"),
     and models never sold with four driven wheels.
  2. `DRV-EVP/-EME/-EMP/-EV` (learned, `learner="drive_evidence"`): the drive type of the cars alike (same
     VIN 1-8 + power; model + fuel + power + engine; model + fuel + power + year; VIN 1-8), >= 5 or 8 cars
     and 98 % agreement, never their own fills as evidence.
  `RuleFamily.guard` keeps every fill consistent with the registry's statement; evidence and reviewed
  families take back the fills of a retired rule on the next apply. Table tightened on the way (MG4 until
  2025, electric GLB rear, Master until 2009 front, more whole-make eras).
- Validation: unit 3,039 and integration 486 passed, ruff clean, mypy clean on api + ingestion. Knowledge
  against cars whose drive type other sources already gave: 40,725 agree, 183 disagree (167 of them
  reviewer rules that say front for the rear-driven XC40 185 kW and the electric CLA 250+). Local copy:
  17,446 of the 23,233 filled (knowledge 12,025, cars alike 4,943, table 478), 5,787 left open (Transit,
  Master from 2010, Yaris Cross and RAV4 hybrids, cars with no make or model). 494,213 of 500,000 cars now
  carry a real drive type. Pilot 20k against the state before: resolved 14,110 -> 14,132; 23 gained,
  1 lost (a 2025 Tucson plug-in whose cars alike are four-wheel drive, matched to a front-drive KType
  before), 0 moved; all 165 newly filled resolved cars agree with their KType's drive.
- Risk / next: local only, not pushed. Two mistakes were caught by the measurement and fixed (a 2020+ BMW
  "coupe" can be the front-driven Gran Coupe; evidence by model and power needs the build year). Kia EV3/EV5
  195 kW and a few others are left open because I am not sure of the variant. The 132 kW XC60/V60 of 2026
  are taken as the plug-in hybrid (four-wheel drive) from their weight and VIN series. On live: deploy,
  then learn + apply `DRV-MY`, `DRV-CAR`, the four `DRV-E*`; each write needs the user's yes.

## 2026-10-03 — Drive type chosen per make and model for cars the registry marks as not four-wheel drive (branch feature/vehicle-corrections)

- `ingestion/vehicle_drive_layouts.py`: a reviewed table of which axle a model drives (726 make + model
  entries, year ranges where a model changed layout or was four-wheel drive only, fuel where the electric
  version differs, and a make without a model only for years in which the whole make agreed). Uncertain
  cases have no entry: Ford Transit, BMW 2 Series, Renault Master, changeover years.
- New rule family `DRV-MY` (`learner="reviewed"`): `learn-vehicle-rules --family DRV-MY --activate` turns the
  table into rules for the make/model/year/fuel keys present; `apply-vehicle-rules --family DRV-MY` fills an
  empty drive type and replaces the generic two-wheel value, only for cars with
  `registry_all_wheel_drive IS FALSE`. A drive type from a reviewer, AIS or a correction is not touched.
  A corrected table statement takes its fills back on the next apply (`retract_retired_fills`).
- Validation: unit 3,000 and integration 482 passed, ruff clean, mypy clean on api + ingestion. On a copy
  of the 500k pilot: 194,116 of 202,273 cars filled (fwd/rwd), 8,157 left open, apply 18 s. Pilot 20k
  against the state before: resolved 13,624 -> 14,093 (68.1 % -> 70.5 %); 471 gained, 2 lost, 2 moved.
  Lost: a Volvo XC90 T6 and a Nissan Sunny estate that sat on four-wheel-drive KTypes although the registry
  says not four-wheel drive (now review). Moved: two Suzuki Swift from the 4x4 KType to the two-wheel one.
  After the fill every resolved filled car agrees with its KType's drive (5,339 of 5,339).
- Mercedes A-Class/CLA/GLA: four earlier reviewer rules (is_4wd 0 + brand MERCEDES-BENZ + a list of model
  texts) set rear-wheel drive for A 200, CLA 180, CLA 200, CLA 200 D, CLA 220 D, CLA 250 E and GLA 250 E
  together with C- and E-Class. Corrected on the local copy with one override reviewer rule (same
  conditions, those seven texts, drive type fwd) through `/v1/match-review` (rule-preview, resolution-rules,
  apply): 559 cars rewritten, C/E untouched. Pilot 20k: 17 of the 18 corrected sample cars went from review
  to resolved, 0 lost, 0 moved, no other car changed (resolved 14,110, 70.6 %). All 559 on their own: 507
  resolve, each to a front-wheel-drive KType.
- Risk / next: local only, not pushed. On live it is the two commands after deploy, and the Mercedes rule
  has to be created there again (it is data, not code; the rules-bundle loader does not carry `override`).
  Each write needs the user's yes. Seen on the way, not changed: about 920 single-motor EX30 carry the
  family name "EX30 Cross Country"; 15,076 cars have no four-wheel-drive statement at all and stay open.

## 2026-10-03 — Tyre sizes re-read alone for records stopped for them (branch feature/vehicle-corrections)

- `northstar-ingest reparse-tyre-sizes [--write]` (`ingestion/tyre_reparse.py`): for records whose latest
  normalization carries `tyre_size_unrecognized`, today's tyre step runs on the raw tyre texts only; where
  they read, a new result is appended with the tyre keys replaced, the reason removed and the status that
  follows (label `<old pipeline>+tyres-v12`). Every other value, candidate and reason is carried over; a
  typo leaves the record as it is. Vehicles are refreshed through `refresh_vehicle_core_records`, and
  `vehicle_facts.norm_status` follows. Dry run by default; a second run writes nothing.
- Validation: unit 2,968 and integration 479 passed, ruff and mypy clean. On a copy of the 500k pilot:
  7,189 stopped records, 4,653 readable now (4,044 resolved, 583 provisional, 26 still in review),
  2,536 still unread, 4,273 vehicles refreshed, 11 s. Pilot 20k against its baseline: 177 cars left the
  normalization stop (40 resolved, 9 provisional, 96 review, 32 hard conflict); 0 lost, 0 moved, no other
  car changed.
- Risk / next: local only. On live it is one command after the code is deployed, and a write that needs
  the user's yes. The other normalization stops (type-approval format, AIS-only cars without reasons)
  are untouched.

## 2026-10-03 — A person corrects a car's data, for one car or the cars like it (branch feature/vehicle-corrections)

- One car (committed earlier on the branch): append-only `core.vehicle_fact_corrections` (set / ignore /
  withdraw per field, release of a stopped car), laid over the car at the matcher's read seam, copied onto
  `core.vehicles`. Added now: a `set`/`ignore` that would take a resolved car's KType away or move it
  answers 409 `confirmation_required` (`before`/`after`) until resent with `confirm_change: true`; the car
  is re-read under its row lock and refused (`evidence_changed`) when its matcher input hash changed; a
  vehicle copy no standing `set` is behind is skipped and reported as `copy_drift`; the merge keeps another
  source's equal value behind a correction so a withdrawal falls back to it; the build month lists 1-12.
- Many cars: scopes (same data, or a ladder of "all cars like this" per field, make first, `is_empty` for
  absent values), a bounded in-process check (one at a time, 500 cars / 240 s, stoppable, each distinct
  matcher input evaluated once with `remember=False`, outcomes gained/lost/moved/worse/same/...), and
  append-only `core.vehicle_correction_decisions` (propose / apply / withdraw; NULL-safe JSON CHECKs;
  nothing applied on a partial check). Apply writes only checked cars, skips cars changed or corrected
  since, leaves harmed cars out unless included, refuses a check that harms as many as it fixes; withdraw
  undoes the whole decision. The lookup names the decision behind a correction. Pilot builder creates both
  tables, copies slice corrections and all decisions, refuses a cut that leaves a corrected car behind;
  the runbook counts corrections and decisions on live before a switch. Details:
  `docs/vehicle-fact-corrections.md`.
- Web: `ns-fact-corrections` (one car, release of a stopped car) and `ns-correction-preview` ("Apply to",
  the check, apply, propose, both undo paths) in the Candidate KTypes panel. The Matched cars dialog shows
  the picked car's own information (`ns-vehicle-facts`: id, status, VIN, values in groups with their
  sources) above its KTypes; the record's wording is shared with the Vehicles panel in
  `core/vehicle-record.ts`.
- Validation: backend unit 2,962 passed (1 expected xfail), integration 474 passed (throwaway databases),
  ruff and mypy clean; web 218 tests passed, build OK. End to end on a copy of the 500k pilot through the
  API: a 255-car check was refused (fixes 76, harms 160), a 5-car decision applied, replayed and undone.
  Not done: an independent review of the corrections code, and clicking the many-car screens by hand.
- A model a person set stands over the registry's model text (`asserted_fields` from the read seam,
  reason `model_asserted_by_person`); measured on the pilot 20k against its baseline: 0 gained, 0 lost,
  0 moved, no car different. A "Decisions" tab on the Vehicles page lists the many-car decisions and
  undoes one as a whole.
- Risk / next: live's corrections and decisions are not carried into a pilot rebuild (no export/import);
  checks live in one API process's memory; measuring a proposal on all cars is not built.

## 2026-10-02 — A person can choose a car's KType on the Vehicles tab (branch feature/manual-ktype-choice)

- New append-only `core.vehicle_ktype_choices` (one linear chain per vehicle by `chain_position`; keys, a
  position-carrying foreign key, CHECKs and append-only triggers; created and verified by
  `migrate-vehicle-core`, which the deploy script already runs). API: `POST` / `GET
  /v1/vehicles/{id}/ktype-choices` (choose, "none of these", withdraw; idempotent by operation id); the
  matching lookup returns the choice, the effective KType and the stale flags. Web: choice controls in the
  matching panel, a "KType choice" filter and a KType column on the Vehicles list. PostgreSQL only: no
  graph, alias, canonical id or ledger write. Details: `docs/vehicle-ktype-choices.md`.
- After review: chain positions (no cycle or detached chain from a multi-row statement), a race-safe repair
  of the vehicle copy, no extra connection for cars without a choice, permanent database refusals answer 422
  instead of "try again", the person's choice leads the panel, the build version is baked into the API image,
  the pilot builder creates and copies the table and refuses a cut that leaves a decided car behind.
- Validation: backend unit and integration suites, ruff, mypy, `nx build` and `nx test` (see the PR).
- Risk / next: **live's choices are not carried into a pilot rebuild yet** (export/import/pinning are not
  built; the runbook stops a switch when live holds any choice). Flagged choices cannot be listed.

## 2026-10-02 — Pilot database builder: a verified 500k slice of the full build (local, uncommitted)

- New `scripts/build_pilot_database.py` (logic in `ingestion/pilot_database.py`): reads the full build
  read-only in one snapshot, creates a NEW database on the same server, builds its schema from the 14
  migration sets, copies class A whole (rules, reviewer decisions, pinned catalog batch, job runs), class B
  for a seeded slice (vehicles and every row of their TS records), leaves class C empty, recounts chunk and
  build counters, moves sequences past the full build's ids, ANALYZEs, then verifies (row counts and content
  checksums per table, slice size and population, catalog batch, foreign keys and undeclared references).
  Dry run by default; `--commit` builds; `--replace` drops only a database this script made.
- Dry run on the full build (seed `northstar-live-pilot-v1`, 500,000 of 6,427,730 registered passenger
  cars): 499,230 TS records, class A 869,902 rows / 0.43 GB, class B 4,158,929 rows / 4.27 GB, 4.69 GB
  estimated; 76 s. `--commit` was not run on the full build.
- Validation: 33 integration tests on throwaway databases, 43 new unit tests; unit suite 2,428 passed (the
  Golf Variant test fails on purpose); ruff, mypy clean. Docs: "Pilot database" in `PRODUCTION_DEPLOYMENT.md`.
- Risk / next: run `--commit` and time it; live-only rules are not in the pilot (diff live's rule tables
  first); one closed review item is on a car outside this seed's slice; batch pickers show full-build counts.
- Review follow-up (same day): default seed is now the match impact seed (imported `SAMPLE_SEED`), so the
  30k sample is the first 30,000 of the slice; a verified build writes a manifest (file via `--manifest`
  and `public.northstar_pilot_manifest` in the pilot) and `--verify DATABASE --manifest FILE` re-checks any
  database read-only (for live after `pg_restore`); a dry run exits 1 when the target is not a pilot build;
  class B predicates are pinned by a unit test; unit test file renamed to
  `test_build_pilot_database_script.py` so pytest collects both. Docs: restore with `--exit-on-error`,
  `ANALYZE`, `--verify`, rule-table comparison SQL, switch by rename, follow-up after go-live.
- Validation: unit suite 2446 passed + 1 expected xfail; builder integration 41 passed (dump/restore through
  the Postgres container); ruff and mypy clean; 500k dry run on `app`: 4.69 GB, 499,516 TS records, 30k
  sample contained. Next: `--size 2000` rehearsal with `--commit`, then the 500k build. Not built yet.

## 2026-10-02 — Batch B: model names, engine codes, tolerances, parsers, promotion gates (local, measured)

- Model names (matcher side): registry spelling of catalog names (CEE'D -> CEED, SANTA FÉ -> SANTA FE,
  Å/Ä/Ö kept), reviewed export names (Golf Plus, New Beetle, ID. Buzz, CC, e-Citigo, Pagode, Sovereign,
  Duett, Scenic E-Tech), glued Mazda numbers, SEAT -> CUPRA; model-vs-brand gate compares by family
  (Pro Cee'd is no Cee'd); a rule-inferred model reached only through an export name needs power or an
  engine code to resolve.
- Engine codes: one relation (`engine_relation`): BMW TU marker and replaced type codes per engine head,
  Saab `/letter`, Mercedes number forms, maker-scoped families (never exact), reviewed alias table
  (`tecdoc/engine_code_aliases.py`, completeness check), strict comma lists, 204PT shared by two engines.
- Tolerances: cc within 3 is unverified, not a conflict (never over an exact-cc sibling held back only by
  its engine code); hp/PS gap (1.01 kW slack) for US makes; Mazda rotary doubled cc; a rounded cc or unit
  gap needs one exact figure beside it.
- Code only, no effect until re-normalization / a new catalog batch: tyre and type-approval parsers;
  promotion gates for single-motor EVs, Table 155 from-only displacement, Petrol/Gas vehicle fuel.
- Measured vs guards-v4: 30k 19,479 -> 20,289 (67.6%; +829, -19, 4 moved), 20k 12,977 -> 13,543 (67.7%;
  +583, -17, 4 moved). Lost are honest ties (V70 II/III 1 cc apart, Ceed 2018, Clubman FWD/ALL4, S60
  T26/T26P) or were wrong (Megane Scenic on a Megane van); moves are Ceed CD -> JD by build month, a 2017
  Santa Fe -> Grand Santa Fé, a 2008 XC70 -> XC70 II. 7 baseline ties resolved, each on new evidence.
- Validation: unit 2,385 passed (Golf Variant test fails on purpose), ruff, mypy; three adversarial reviews.
- Next: 500k live pilot (random registered passenger cars, built locally, loaded onto live); the wrong-fill
  data step still has to be run by the user (scratch wave1_data.sh).

## 2026-10-02 — Wave 1 (precision first): matcher guards measured; wrong-fill guard ready (local, uncommitted)

- Non-tie diagnosis (21 agents, 20k): 3,859 unresolved non-tie cars explained; ~935 resolvable by code/data,
  ~500 more by stakeholder decisions, ~490 genuine non-matches; ties stay manual (user decision).
- Matcher guards (`fuzzy_matching`, `match_run_adapters`): plug-in power lead, electrification conflict
  (TecDoc engine type 046-049 vs registry), conflict-free suggestion over a hard conflict (inside the
  registry family), reading disagreements (IONIQ 5 -> 6, V60 vs V60 CROSS COUNTRY), no fall-through to
  another reading once a guard held one back. Measured vs final-v4: 30k 19,640 -> 19,479 (-161),
  20k 13,079 -> 12,977 (-102); 0 gained, 0 moved; every lost car is a wrong match before except 3 V60 CC
  B5 and 1 Lexus CT the registry calls a plug-in. Chunk SIGNATURE_VERSION 3.
- Wrong-fill guard (`vehicle_model_guard`, patterns, rule eras): reviewed rule by rule; dry run takes back
  20,845 wrong fills (EX30 CC 13,929, MAZDA2->CX-3 2,727, CC->Passat 1,113, 230->SL 801, Sportage->Sorento
  710...). The data step (retire 96 changed rules, learn/apply/check, refill ~19.3k) awaits user approval;
  model families snapshot in scratch. Caravelle/Multivan naming pending.
- Validation: unit 1,895 passed (Golf Variant test fails on purpose), integration 189 passed, ruff, mypy.

## 2026-10-01 — Build months made precise: model lines, engine sizes, estate names (local, uncommitted)

- Months choose only within the car's model line (`_model_line`: TecDoc name without chassis code,
  generation and the car's own body name). Own-line KTypes are penalized only when a conflict-free KType
  of that line covers the build month; other lines whenever outside; conflicting KTypes always. Tolerance
  stays 0 months (`FuzzyMatchConfig.production_month_tolerance`, documented with the measurements).
- Body names: another body's word keeps a separate line (registered SUV: GLC Coupe != GLC); makers' estate
  names (T-Model, Turnier, Grandtour, ST, ...) count as bodies; Sportback/SC/GTC/Allroad do not.
- Registry text: a decimal number ("2.0", "1,6") is an engine size, never a model number ("QASHQAI 2.0" is
  no Qashqai +2).
- Final (prod-v4 vs prod-v2): 30k 64.2% -> 65.5% (+410/-32/8 moved), 20k 64.1% -> 65.4% (+279/-24/6).
  Against plain months 14 wrong moves taken back (Ibiza SC, Pajero Sport, Tiguan Allspace, GLC Coupe,
  Qashqai +2, C4 Cactus). Every lost/moved car checked; local API points at prod-v4.
- Next: non-tie diagnosis workflow (candidate-only, power, model missing/text, normalization, engine,
  body, other conflicts, rule-filled models) -> plan; ties stay for manual choice.
