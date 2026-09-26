# Last Context

Keep the latest 10 task entries only.

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

## 2026-09-26 — STEP VIN export (XML) checked against TS data and the matcher

- Parsed the 16 GB STEP export (10,814,705 VINs) into local-only `sandbox_step_xml.vin_export`; no real table changed.
- Found 6,490,789 of 6,532,590 TS cars by VIN. For passenger cars: engine code 0% -> 95.6%, model year 15.8% -> 99.6%, kW 94.8% -> 99.7%; kW and model year agree with TS on 99%+.
- Matcher before/after on 3,000 random passenger cars (prod-v2 catalog): single KType 882 -> 894, several 529 -> 363, none 1,131 -> 1,285. 120 single matches are lost to engine-code conflicts.
- Risk / next: ~305k cars conflict only on format (XML `BHZ` vs TecDoc `BHZ (DV6FC)`), and ~320k carry codes TecDoc lacks. Fix the matcher's engine comparison before importing engine codes.

## 2026-09-26 — Local environment replaced with a full copy of live

- Postgres: streamed a read-only `pg_dump` of live (6.2 GB compressed) and restored it; the old local `app` database was dropped and the restored copy renamed to `app`, so `DATABASE_URL` is unchanged.
- Neo4j: exported live's graph through a read-only session (no live downtime) and loaded it into the local Neo4j; `apps/backend/.env` now points at the port the local container actually uses (bolt 7689, http 7475).
- Validation: tables/indexes/constraints/triggers, raw, normalization, vehicle_facts, rules, field resolutions, TecDoc candidates, review queue and max IDs all equal live; Neo4j 116,959 nodes / 199,682 relationships equal live.
- Risk / next: local-only TecDoc v3/v4/v5 re-promotion batches and 11 match runs are gone; live Neo4j has no uniqueness constraints (local has 9); `~/NorthStar-local-backups/` still holds the old graph dump for manual deletion.

## 2026-09-23 — Vehicles: TS-to-TecDoc matching diagnostics (local only)

- New `vehicle_matching` feature over the audit's own `TecDocDryRunEvaluator` (pinned
  candidate catalog, active rules, reviewed aliases, fuel/drive alignments; nothing
  reimplements matching). `GET /v1/vehicles/matching/lookup` (plate/VIN or exact
  `source_record_id`) shows what the matcher saw and every KType it weighed;
  `POST /v1/vehicles/matching/summary` runs it over a Vehicles filter as a polled
  background job and counts one / several / none / not-matchable, naming the fields
  behind each gap. UI: "Candidate KTypes" in the record panel and a "Matching" view.
- Findings on local data (1,000 Volvos): 588 one, 246 several, 166 none; 145 of the
  `none` conflict on bodywork (estate vs SUV), 137 of the `several` are separated by an
  engine code the car lacks. Mixed brands also show V70 competing with XC70 on
  identical specs -- a matcher-side ambiguity, not missing data.
- Validation: 17 backend + 9 web tests new; 1256 backend, 40 web, ruff, mypy, prod build;
  every endpoint and the UI checked against the real local database.
- Not yet for live: no plate/VIN index on `vehicle_facts` (lookup would scan 6.5M rows);
  jobs live in API process memory. Matcher costs ~0.1s a car (1s+ for Mercedes).

## 2026-09-23 — Vehicle type filter on TS data

- Added the Vehicle type dropdown to `/ts-data`, sharing its definitions with Vehicles via
  `core/vehicle-scope.ts`. It narrows everything the screen shows (count, list, facets,
  unresolved summary, gap groups) but is never part of a rule: the resolver still reads
  `FilterState.payload()`, and the rule endpoints reject `vehicle_scope` as a condition
  anyway. Defaults to "All vehicles" here (Vehicles defaults to "Passenger cars") so the
  counts a rule is authored from match what the rule will touch. Changed the same day
  to default to "Passenger cars", matching Vehicles, at the user's request.
- Validation: 30 web tests (7 new), production build; checked in the browser that the
  scope changes the counts while "Which cars" stays unconditioned.

## 2026-09-23 — Vehicles tab rebuilt: passenger cars first, schema applied on deploy

- Re-landed the Vehicles tab (reverted 2026-09-22 after its first release 503'd on live:
  the deploy shipped code reading `canonical_fuel`/`canonical_transmission`/
  `canonical_euro_class`, but nothing ever added those columns to production's
  `core.vehicle_facts`). Root fix: new `migrate-vehicle-facts` CLI command (schema only,
  idempotent) now runs in `infra/production/deploy.sh` before `up -d`, so a column a
  feature adds exists before the code that reads it goes live.
- New `vehicle_scope` column (passenger / motorhome / special_modified / test_record /
  other_category), derived from the pipeline's own `record_route` and
  `parts_matching_exclusion_reason` plus non-M1 EU categories -- never TecDoc's `is_pc`,
  which marks motorcycles as passenger cars. Vehicles defaults to "Passenger cars" via
  `vehicle_scope NOT IN (excluded)`, which keeps un-backfilled NULL rows visible; a
  notice says so until the backfill has run.
- New `backfill-canonical-vehicle-facts` command updates only the canonical-only columns
  (resumable, disk-guarded) instead of a full `refresh-vehicle-facts`. It is not run by
  deploy -- run it deliberately on production after merging.
- Validation: 1237 backend unit tests, ruff, mypy, 23 web tests, production web build;
  reproduced the live failure locally (table predating the column) and confirmed the
  migration adds it and the page serves without 503. The scope expression was verified
  read-only against real local rows; the local backfill itself was blocked by the
  disk guard (host disk at 99%).

## 2026-09-21 — Correcting a value the TS data screen already has

- Added an override mode to resolution rules so a reviewer can fix a wrong value, not
  only fill a missing one: the record panel on `/ts-data` now offers **Edit** beside
  every resolved field, which pins the car's brand and model into the filter and opens
  the same resolver panel in correction mode. An override rule selects cars whose
  effective value differs from the one asserted, supersedes the resolution they carried,
  and writes its own; the projection now reads `coalesce(r_x, n_x)`, so a reviewer's
  assertion outranks the derivation it corrects, and Retire puts the derived value back.
  Correcting is opt-in, stored immutably on the rule (`core.match_resolution_rules.override`),
  and refuses to run until the exact value has been previewed. Validation: ruff, strict
  mypy, 1213 backend unit tests, 18 web tests, Angular build. Remaining step: the flipped
  effective-value index ships with the next `refresh-vehicle-facts` run, which runs the
  vehicle-facts migrations first; until then a `normalized manufacturer` filter is
  unindexed. Nothing in production was changed.

## 2026-09-06 — Corrected unresolved-fields ownership

- Confirmed from historical implementation `25cc983` that the intended rule generator is the population-first **Unresolved fields** workflow: unresolved field/value populations, discriminators, rule preview, save, and save-and-run. Corrected Angular navigation and copy so `/coverage` is **Unresolved fields** and `/chunks` is **Match review** for TS-to-TecDoc blockers. The population-first backend endpoints (`/v1/match-review/unresolved`, `/discriminators`, `/rule-preview`, resolution-rule save/apply) are not yet present in the current backend branch; only the coverage shell is currently wired. Angular build passes.
