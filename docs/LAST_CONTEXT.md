# Last Context

Keep the latest 10 task entries only.

## 2026-10-07 — Six matching decisions, as proposals in force (branch feature/matching-decisions, on feature/ais-make-codes)

- Six rulings a stakeholder owns, each switchable and marked `claude-proposal-2026-10-07`: (1) a
  candidate-only KType that is the car's only fit, with every routing gate met, is its match (reason
  `candidate_only_sole_fit`); (2) a registry MPV fits a TecDoc SUV (a row in `core.tecdoc_resolution_rules`,
  not code); (3) reviewed pairs of rated and peak power for six electric models
  (`tecdoc/power_equivalences.py`) and a 2 % power tolerance for electric cars where the KType's figure is
  the only one of its model that near; (4) Renault `5AQ-60` names the motor family; (5) one listed motor
  on a four-wheel-drive electric KType is family evidence (Kia EV9); (6) a car built before 1975 is not
  contradicted by its power while no plausible KType carries the figure. Details and switches:
  `docs/vehicle-match-decisions.md`. Stakeholder wording: the shared document "Vehicle matching: six
  decisions needed".
- Reference check for (1): 20,000 cars with an engine-confirmed candidate-only KType, engine code hidden:
  11,094 accepted, all to the known KType.
- Impact reports on the full local register: seeded 30k 70.8 % -> 73.8 % (+908, 0 lost, 0 moved); random
  20k 70.7 % -> 73.6 % (+578, 0 lost, 0 moved). Matched control groups kept every match.
- All 1,173,607 cars the decisions can reach were matched again (`refresh_vehicle_match_results
  --vehicles-from <file> --force`, new option; 3 h 6 min with 6 workers): 191,834 gained, 0 lost, 2 moved
  (both from a model's van KType to its SUV KType). Register resolved 70.80 % -> 73.79 %; cars AIS added
  43.56 % -> 55.16 %. By decision: 146,822 / about 20,000 / 8,671 / 8,825 / 0 (6,579 to several) / 7,195
  (32,147 to several).
- Vehicles tab: the breakdown counts and lists the cars matched as the only fit ("Matched - to check",
  `resolved_only_fit` in the overview).
- Validation: 3,665 backend tests pass in one run (1 expected failure), ruff and mypy clean; web 228 tests
  pass, build clean.
- Analysis of the 1.05M cars with several KTypes (not acted on): about 275,000 hybrids tie because the
  registry's engine power and TecDoc's system power cannot be compared (learning it from matched cars
  settles only about 7,500 safely; the fix is the engine's own power in the catalog); about 170,000
  petrol / diesel cars tie on model or year alone (Passat estate, V60 Cross Country, BMW 1 Series);
  29,500 tie on KTypes that differ in nothing compared (Kia Niro 20,000).
- Not pushed, not on live. Live gets this as a new slice of the local full database after the work done
  on live since the last switch is carried over; whether the six decisions go live before the
  stakeholders confirm is the user's call.
- Local snapshots: `public.match_results_after_final_20261007` (before the decisions) and
  `match_results_after_decisions_20261007`.

## 2026-10-07 — AIS cars: names divided, hybrid type and engine codes from the cars alike, two matcher fixes (branch feature/ais-make-codes)

- Matcher: a brand text that is only the make's name ("MINI") is no longer read as a catalog model in the
  check between model text and brand text. Every car it can touch (19,558 of MINI and Daimler): 59 gained,
  0 lost, 0 moved; the 30k and random 20k samples: no change.
- Name division applied locally (`repair-ais-vehicles --write`): 120,763 AIS vehicles got a model text.
  With the matcher fix: 1,380 gained, 41 lost (17 of them V60 Cross Country that had resolved to the plain
  V60), 0 moved. AIS cars stopped before matching: 20,169 -> 11,800.
- Hybrid type (`ELT-GC`, `ELT-VINP`, `ELT-ENG`, `ELT-VAR`, learner "electrification"): the registry states
  a hybrid in a field AIS does not have, and AIS gives a hybrid that does not charge no second fuel. 263,458
  fills locally (12,363 on registry cars with electricity and no stated type); 96,732 AIS cars were hidden
  hybrids and also got electricity as second fuel and the hybrid's fuel tokens. On held-out registry cars
  the keys were right for 99.96-99.99 %. Result: 13,195 gained, 2,108 lost, 1 moved.
- Matcher input: `electrification_type` is now among the vehicle fields handed to the matcher
  (`MATCHER_FIELDS`); a car AIS added had it on the vehicle only. Samples: +4 / -15 (30k), +2 / -14 (20k),
  0 moved; every lost car was a mild hybrid resolved to a plug-in KType or the reverse.
- Engine code where the older families are silent: `ENG-VINP` (now with the build year) and `ENG-MP`:
  16,922 fills locally. Mostly electric Volvo EX40 and Polestar 2 / 4 whose one KType the motor code confirms.
- Displacement fills (`CCM-*`) were built, measured and removed: 784 gained, 2,306 lost. Right for 99.9 %
  of the registry cars that state a displacement, wrong often enough for the cars that lack one (a Nissan
  Almera 1.6 learned as 1,332 cc). 443,054 fills taken back locally.
- Whole AIS work against the baseline of 2026-10-06, local full database: AIS cars 28,223 gained, 2,462
  lost, 9 moved; registry cars 950 gained, 50 lost, 6 moved. Resolved: register 70.39 % -> 70.80 %, AIS
  cars 39.6 % -> 43.6 %; AIS cars stopped before matching 53,756 -> 11,800. The lost AIS cars are mostly
  wrong matches the hybrid type now prevents (Cupra Terramar 1,276, Cupra Leon 1,245, VW Tiguan 849,
  Hyundai Tucson 303) and mild hybrids that are tied as their registry twins are (Mercedes CLA / GLA).
- Validation: 3,622 backend tests pass in one run (1 expected failure), ruff clean, mypy clean.
- Open, each needs a decision or reviewed knowledge, not code alone: power on veteran cars (about 40,000:
  Volvo Amazon / PV, Saab 99 / 900, Mustang) and on electric cars (about 15,000 AIS: Audi A6 e-tron,
  Toyota bZ4X; partly 2025-26 variants the catalog lacks); Peugeot 2008 / 3008 / 5008 body (10,500 AIS, the
  pending MPV / SUV ruling); Renault Zoe engine codes (8,900: "5AQ-60" against "5AQ 601 / 605"); Kia EV9
  with two motor codes (5,100); the candidate-only decision (Polestar and others, about 74,000 AIS cars
  with one KType not accepted). Battery electric AIS cars still carry no hybrid type (no effect on matching).
- Local snapshots: `public.match_results_baseline_20261006` and `match_results_after_{makecode,names,
  hybrid,gaps,final}_20261007`; row backups `ais_make_code_before_20261006`, `ais_names_before_20261007`.
- Not pushed, not on live. On live the order is: deploy, `learn-vehicle-rules --family TSC-BT --family
  ELT-GC --family ELT-VINP --family ELT-ENG --family ELT-VAR --family ENG-VINP --family ENG-MP --activate`,
  `repair-ais-vehicles --write`, `check-model-fills --retract`, `apply-vehicle-rules`, then a rebuild of the
  stored match results (two matcher changes).

## 2026-10-06 — AIS cars: make code and name read right, and the cars already created repaired (branch feature/ais-make-codes)

- Cause found: the AIS export writes the registry's make code (two characters, three for newer makes) and
  the six-digit group number as one string; the import cut it after two. "POL021900" (Polestar) became make
  `PO` (Pontiac's code) and group `L021900`. 44,754 AIS-origin cars: stopped before matching
  (`normalization_review_required`) and found by no rule keyed by make and group code.
- Second finding: the AIS car name is the registry's brand text followed by its model text. Where the group
  was unknown the whole name stayed in the brand text and the car had no model text (about 122,000 cars).
- Code: `AisRecord` splits the group code from its end; the name is divided at the group's brand text or at a
  brand text the registry writes for the make (new completion family `TSC-BT`, 1,800 rules locally);
  `repair-ais-vehicles [--write]` (`ingestion/ais_vehicle_repair.py`) describes the vehicles already created
  again from what they still hold. It merges only the fields that description decides, leaves alone what a
  rule, a reviewer or a person supplied since, and replaces the normalization status only when the stored
  values explain the status the vehicle carries (the raw fuel and gearbox codes are not stored).
- Validation: 3,615 backend tests pass in one run (1 expected failure), ruff clean, mypy clean on
  api + ingestion + northstar.
- Local full database (`app`), make-code repair only: 44,754 vehicles repaired (model text filled on 40,608,
  type code 35,451, variant 32,372, displacement 13,415); then `apply-vehicle-rules` (973 engine codes),
  `check-model-fills --retract` (253 models the new model texts contradict, 253 refilled by text) and the
  match-result refresh. Against the stored baseline, those cars: resolved 2,166 -> 16,810 (gained 14,648,
  lost 4, moved 0), not matchable 36,672 -> 3,085, one KType not accepted 947 -> 15,264. No other car
  changed. Register: resolved 70.39 % -> 70.62 %; AIS-origin cars 39.6 % -> 41.9 %.
- The 4 lost are Lynk & Co 02 (electric, 200 kW, 2024) whose registry group says "LYNK & CO 01": the model
  check trusts the registry text. A correction per car in the Vehicles tab fixes them.
- Name division is not applied locally yet. Dry run: 120,763 vehicles get a model text. Preview of 3,000
  cars inside a rolled-back transaction: 24 gained, 14 lost, 0 moved. All 14 lost are MINI "COOPER E": the
  matcher reads the brand text "MINI" as the catalog model "MINI (F56)" and reports
  `model_source_evidence_conflict`; the same stops 187 TS MINI cars today. Next: fix that in the matcher
  (measured), then apply.
- Remaining on the repaired cars: 12,720 have one KType that is candidate-only (Polestar 11,630: open
  stakeholder decision); conflicts on power (Zeekr, XPENG, Cupra), body (Zeekr, BYD) and engine code
  (Polestar, BYD); 3,085 still stopped (Cupra 1,584, BYD 427, XPENG 98: released by the name division;
  Leapmotor and Seres 506: makes the normalizer does not know).
- Performance note: selecting the changed cars for a refresh takes about 10-15 minutes on the full database
  whatever their number; the per-page select is as slow as the count.
- Not on live. The pilot's share of the wrongly cut cars is there too; the repair on live needs a yes.

## 2026-10-03 — Stored match results: statistics and car lists read from a table (branch feature/vehicle-match-results)

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
- Web: Vehicles > Matching now shows the stored overview (`ns-match-results`) with the cars behind every
  number (`ns-match-result-cars-dialog`); the live sample run is folded below it. 243 web tests pass and the
  build is clean; one live-API test (`Rules lists the rule catalog`) timed out while the fill held the CPU.
- Saves keep the row current: the choice, correction and decision services call `MatchResultSync` after
  their transaction (never fails the save; more than five cars refresh on a thread). The Vehicles list
  filters on `match_result` (probes of the stored results, no join) and shows the accepted KType or the
  stored state per row. Unit 3,066 and integration 517 passed; web 244 passed, build clean.
- 2026-10-04: full fill of `northstar_pilot_corr` done (490,000 matched in 3 h 25 min, 4 workers): resolved
  351,693 (70.3 %), several 80,590, none 30,496, one unconfirmed 19,043, not matchable 18,178; table 389 MB.
- 2026-10-04: the Matching tab is gone; Vehicles > Cars holds it all: counts per state above the list
  (`/counts`, 0.3 s for 500k), the breakdown on demand (`/overview`, one pass, ~2 s for all cars), cause
  filters (`match_missing_field`, `match_conflicting_field`, `match_candidate_count`, `match_ktype`, ...) and
  the possible KTypes in the KType column. The on-screen sample run was removed (its API stays).
  Unit 3,066 and integration 522 passed; web 220 passed, build clean.
- Next: refresh after bulk rule application or re-normalization is still the normal run; the unused
  summary-job API and `/cars` endpoint could be removed. Details: `docs/vehicle-match-results.md`.

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
