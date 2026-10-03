# Last Context

Keep the latest 10 task entries only.

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

## 2026-10-01 — prod-v4 (v2 + TecDoc months) and build-month matching (local, uncommitted)

- Composed `tecdoc-0326-canonical-full-prod-v4-20261001`: every row of prod-v2 (incl. the 829 transmission
  entities and gearbox attributes from Table 547) + `month_from`/`month_to` from prod-v3. Verified: equal
  counts, 0 rows differing from v2 beyond the month keys, 0 month values differing from v3, 0 links
  differing. The Mac mini's 0326 delivery lacks Tables 547-549 (why v3 had no transmissions).
- Ported the parked month code (`wip/build-month-matching` 2ac626d) onto the working tree.
- 30k 64.2% -> 65.4% (+410 / -42 lost / 16 moved); independent 20k 64.1% -> 65.4% (+279 / -30 / 11).
  Lost go to review, mostly builds 1-3 months before TecDoc's start month; some were wrong before (i20
  built 04/2014 had resolved to the i20 II starting 11/2014). Moves mostly right (Legacy V, XC60 I);
  4 doubtful (Pajero -> Pajero Sport, Ibiza -> Ibiza SC).
- Boundary tolerance on the changed cars (both samples): 0 months +686/-72/27 moved; 1 month
  +563/-52/17; 2 months +444/-37/16; 3 months +341/-21/14. Decision pending; the API still pins prod-v2.
- The report refuses to compare runs on different catalog batches; compared car by car with a scratch script.

## 2026-10-01 — Ford Mustang, BYD Atto 3, and model numbers in family comparisons (local, uncommitted)

- FORD MUSTANG: blocked by six TS cars filed "Gt" (Mustang GTs whose model text "GT 500" TS read as the
  Ford GT), not by the Mach-E, which has its own text. Reviewed exception (`MISREAD_STATED_FAMILIES`) plus
  a fuel check in the guard (`REVIEWED_ELECTRIC_FAMILIES`): 5,356 cars -> Mustang, the one electric car
  (VIN 3FMTK, a Mach-E) refused. A general "count only brand-text-only siblings" fix was tried and
  reverted: "BMW X3" is also the brand text of 498 X4s.
- BYD: TecDoc has no ATTO 3; its "YUAN PLUS" (2022-, EV, 150 kW, FWD) is the Atto 3 -> reviewed export
  name. All Atto 3s now get YUAN PLUS as top candidate but stop on body: registry MPV vs TecDoc SUV (a
  body ruling for the data owner).
- Model numbers now count: the matcher's model-word reading no longer binds "ATTO 3" to ATTO 2, and the
  guard's family comparison separates ID.4/ID.5, Ioniq 5/6, Model 3/Y. It caught 4,028 wrong fills
  (3,682 "ID.4" that are ID.5s, 346 "DS 7 Crossback" that are DS4/DS3), retracted and refilled.
- MINI: a family named like the make answers only when no other family's word follows (7 Countryman/
  Clubman rules retired, 88 fills).
- FORD USA: TecDoc files every Mustang, the Mach-E, and the US Explorer/Edge/Probe under "FORD USA"; the
  matcher now looks there (`REVIEWED_SISTER_MAKERS`) only when the car's model is no family under "FORD".
  30k: 64.0% -> 64.2% (+47 / 0 lost / 0 moved; all 47 agree on year and power). The guard keeps reading
  under the make ("CUSTOM" on a Transit Custom would name a 1950s Ford).
- Local DB: no model family 224,888 -> 221,279; check 0 contradictions. Tests 1,808 pass.
- Independent 20k sample (new seed `northstar-random-20k-2026-10-01`, ~90 cars shared with the 30k): 64.1%
  resolved, agreeing with the 30k's 64.2%. Hard conflicts 1,115: power 487, engine code 356, displacement
  208 (197 within 10 cc -- exact-equality comparison), year 142. Engine codes: 35 are the same engine in
  another format ("H5H-470, H5H-480"), BMW "M57-TU2D30" vs "M57 D30" ~70. Body conflicts 288 (MPV vs SUV 65).

## 2026-10-01 — More model family gaps: chassis codes, VIN model year, classics (local, uncommitted)

- Guard reads "GR" as Grand when the spelled-out text names the filled family (Grand Voyager/Vitara).
- New family MOD-VINY (VIN descriptor + model-year character; holdout 99.98%, 99.5% where the VIN
  alone is ambiguous). Reviewed chassis codes checked against TecDoc's codes (Honda RD/EU/CG..., Renault
  BA/JA/KA/KC, Ford P3TS/GNR), aliases (Trans Am -> Firebird, MCC -> City-coupe, M3 -> 3 Series),
  Stellantis "e-" versions, Volvo P120/111xx (Amazon, PV 544), classic names (Cortina, Spitfire,
  Valiant, Fiat 124/128, Austin/BMC Mini...). An alias never competes with a TS name ("allroad").
- Caught: BMW "2002"/"1602"/"2000" are TecDoc versions, not families -- a filled "2002" lost a 2002
  Turbo on the 30k; names removed, 109 rules retired, 2,004 fills taken back.
- Local DB: registered passenger cars without model family 256,738 -> 224,888 (279,453 at the start of
  this pass); check-model-fills 0 contradictions. 30k 64.0% -> 64.1%.
- Validation: 1,787 tests pass; ruff/mypy clean; Golf Variant test still awaits a decision.
- Needs decisions: classic Mercedes numbers (~40k, TS has no names before ~1990), classic VW Type 1
  (~20k) and 1500/1600 (~6k), FORD MUSTANG text shared with Mach-E (~5k), campers/ambulances (~10k).

## 2026-09-30 — Model family and manufacturer gaps across the registry (local, uncommitted)

- Reviewed model names (`REVIEWED_MODEL_NAMES`, ~70 makes: ID.7 Tourer, EV3, EV9, bZ4X, Tipo, Punto,
  Atto 3...), longest-name reading of model and brand text (trims, repeated makes, make aliases like
  "VW", "GR" = Grand, Volvo "S + V70", Lexus "IS200", one chassis code in front: "FORD DAW FOCUS"),
  most-used spelling per name ("RAV4" not "Rav 4"), Volvo classic codes (Amazon, 140, 164, P 1800,
  Duett, 340, 440, 460), Saab model numbers. New families MFR-BW (manufacturer from the brand text's
  first word: AIS codes "PO"/"CU" missed Polestar/Cupra) and MOD-BRT (brand text reader).
- Guarded by review: dropped Trans Am (TS files it under Firebird; 915 fills taken back), "SLC" after a
  number (450 SLC is TecDoc's SL Coupe), "E-" prefixes, codes that start a family name ("ID. POLO").
  Changed/retired rules retired with retire_rule before re-applying. check-model-fills: 0 contradictions.
- Local DB: registered passenger cars without model family 648,695 -> 279,453; without manufacturer
  39,824 -> 3,939. 30k: 63.5% -> 63.9% (+137 over five runs, 0 lost, 0 moved).
- Validation: 1,767 tests pass; ruff/mypy clean; `test_source_model_rules` still awaits the Golf Variant
  decision.
- Open: classic VW Beetle naming (Beetle vs TecDoc KAEFER, ~20k), FORD MUSTANG text shared with Mach-E,
  guard reading ignores "GR" (Grand Voyager/Vitara refused, ~3.7k), 173k pre-1990 classics, 13.8k with
  only the make, camper/ambulance conversions left unfilled on purpose.
