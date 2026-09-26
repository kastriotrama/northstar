# AIS VIN export: TS and TecDoc improvement plan (v2, measured on live data)

| Field | Value |
|---|---|
| Status | Proposed plan, not started |
| Date | 2026-09-26 |
| Supersedes | `AIS_VIN_EXPORT_ENRICHMENT_PLAN_2026-09-26.md` (v1, measured on a partial local import) |
| Data | Full local copy of the live database (restored 2026-09-25): 6,532,590 vehicles, 6,444,864 passenger cars |
| Source analysed | `exported-2026-09-19_11.05.36 testing.xml` (Stibo STEP export, 16 GB, 10,814,705 VINs) |
| Matcher | Real `TecDocDryRunEvaluator`, catalog `tecdoc-0326-canonical-full-prod-v2-20260914` (the batch live uses) |
| Related | [Stakeholder decision guide](STAKEHOLDER_DECISION_GUIDE.md), [Fuzzy matching contract](fuzzy-matching-contract.md) |

## 1. Summary

v1 measured 541,489 vehicles from a partial local import against an old local
TecDoc batch. v2 repeats the analysis on the full live data set and the live
catalog, and simulates every proposed fix with the real matcher on a random
sample of 30,000 passenger cars.

**Today 16.0% of passenger cars resolve automatically to one k-type (about
1.03M). The measured fixes below take that to about 43–45% (about 2.8M), while
the share of resolved k-types contradicted by the AIS engine code falls from
4.4% to about 2%.**

The value of the XML is not where v1 put it:

- **The XML's real value is the engine code as evidence, not as a filled field.**
  It confirms which engine a car has, which settles the "candidate-only"
  k-types TecDoc links to several engines. That is the single largest gain
  that needs the XML (+14.9 points, about 960k cars).
- **The largest gain overall needs no XML at all**: a body-type fallback
  (+9.7 points, about 623k cars) with accuracy equal to today's.
- **Importing the engine code naively gives zero net gain.** It fixes as many
  matches as it breaks, because the matcher compares engine codes too strictly.
  The comparison must be fixed before, or together with, the import.
- **The XML does nothing for the model gap.** Its "name of the car" is the TS
  `brand` and `model` text joined together. The model gap is ours to solve
  from our own data.
- **The XML is 2¾ years newer than our TS data** (Sep 2026 vs early Dec 2023).
  It holds 660,840 active passenger cars we do not have. See the
  [field-by-field analysis](AIS_VIN_EXPORT_FIELD_ANALYSIS.md).
- **The TecDoc rebuild in v1 step 1 is already done on live.** v1's "new"
  catalog (62,770 k-types, 57,727 with an engine, 14,957 candidate-only) is
  identical to the live `prod-v2` batch built on 2026-09-14.

## 2. Method

- **Population:** all 6,444,864 passenger cars (`vehicle_scope = passenger`) in
  the full live copy. Field and status figures are exact counts over all of them.
- **Matching:** a random sample of 30,000 passenger cars, each evaluated by the
  real matcher once per variant. Shares have a 95% margin of about ±0.5
  points. "Scaled" figures multiply the share by 6.44M.
- **"Resolved"** is the matcher's automatic outcome (`resolved`): score ≥ 0.90,
  clear margin, no conflict. "Provisional" and "review" are counted separately.
  v1's "1 k-type" counted something looser.
- **Accuracy check:** each resolved k-type is compared with the AIS engine code.
  "Differs" means the k-type's TecDoc engines do not include the car's AIS
  engine. This check is independent only for steps that do not use the engine
  code (today, hybrid kW, body fallback, model). For steps that use it, it only
  shows consistency. It is a proxy: a k-type can carry the right engine and
  still be wrong on body or year. Step 0 adds a reviewed reference set.
- **Nothing was written to real tables.** The XML lives in the local-only
  schema `sandbox_step_xml` (`vin_export`, 10,814,705 rows).

## 3. What the XML adds (exact, full population)

| | Count | Note |
|---|---|---|
| Our vehicles found in the XML by VIN | 6,490,789 of 6,532,590 (99.4%) | |
| Passenger cars with an AIS engine code | 6,161,021 (95.6%) | TS has no engine code at all today |
| – of which junk (`1`, single letters, < 3 characters) | 277,528 | Must be filtered |
| – exact match to a TecDoc engine code | 5,535,425 | |
| – match only without TecDoc's bracket part (`BHZ` vs `BHZ (DV6FC)`) | 305,453 | Format difference, not a real conflict |
| – not in TecDoc at all | 320,143 | e.g. `3DU`, `EE20`, `B205EB`, `B5252S` |
| Model year: filled | 15.8% → 99.6% | The matcher does not read model year |
| kW: filled | 94.8% → 99.7% (+313,443) | 310,137 are EVs whose power TS already has (`ev_power_kw`); own-data fix |
| Month of manufacture | 5,214,319 cars | A copy of the TS build month (TS has it for 81%); no new information |
| Marked deleted (`Import_Deleted`) | 672,563 | Deregistered since Dec 2023; v1 said 47,800 (partial data) |
| Marked scrapped (`Skrotad`) | 513 | |
| XML plate differs from ours | 5,983 | v1 said 1,540 |
| Vehicle type differs | 11,580 "passenger" are TR, 1,044 are LB in the XML | Feeds the vehicle-type work |
| Model / name of car | TS `brand` + TS `model` joined | No new model information; 155,927 cars have TS model text our normalization does not map (own-data fix) |
| Kerb weight, max weight, length | ~6.39M each | New attributes our TS extract lacks (display, search, vehicle-type checks) |
| Active passenger cars missing from our TS | 660,840 | Registered 2024–2026; our TS snapshot ends in early Dec 2023 |

Duplicate TS rows are not the problem v1 describes: live has 5,914 extra rows
for a repeated VIN, not 360k.

## 4. Measured effect of each step (30,000-car sample)

Each row includes the rows above it.

| Variant | Resolved | Scaled | Provisional | One compatible | Resolved k-type differs from AIS engine |
|---|---|---|---|---|---|
| V0 Today | 16.0% | 1.03M | 12.3% | 28.8% | 4.4% |
| V1 + XML engine/kW, naive fill | 16.0% | 1.03M | 11.5% | 28.8% | 0.0% (consistency only) |
| V2 + junk filter, tolerant engine comparison | 17.5% | 1.13M | 12.9% | 31.3% | 1.2% |
| V3 + hybrids: no kW comparison | 19.0% | 1.22M | 13.0% | 33.2% | 1.1% |
| V4 + body fallback | **28.6%** | **1.85M** | 22.5% | 44.7% | 2.1% |
| V4 + candidate-only k-types confirmed by engine (upper bound) | ~43.5% | ~2.80M | | | see below |
| + model from brand text via reviewed rules (upper bound) | ~45% | ~2.9M | | | see below |

What each step does in detail:

- **Naive import (V0 → V1): +594 resolved, −595 lost, net 0.** 578 of the lost
  become hard engine-code conflicts. Some were wrong matches (today 4.4% of
  resolved differ from the AIS engine), but most are format differences.
  10 cars move to a different k-type, 7 of them onto the AIS engine.
- **Tolerant engine comparison (V1 → V2): +454, −5, net +449 (+96k).**
  Compares with and without TecDoc's bracket part, the first token
  (`K9K 276` → `K9K`) and the hyphen suffix (`D4FB-H` → `D4FB`), and drops junk.
  The simulation also treated codes unknown to TecDoc as "missing". 61 of the
  454 gains come from that and point to a k-type with a different engine, so
  **an unknown code must not allow automatic resolution**: it should cap the
  car at provisional.
- **Hybrid kW (V2 → V3): +475, −46, net +429 (+92k).** 473 of 475 gains agree
  with the AIS engine. Hybrids resolved: 249 → 678 of 3,226. The simulation
  skipped kW for hybrids entirely, which is an upper bound. The real fix is
  comparing TS kW with the combustion engine's power.
- **Body fallback (V3 → V4): +2,902, −0 (+623k).** When no plausible candidate
  matches the TS body type, match without body. 2,555 of the gains agree with
  the AIS engine and 107 differ (4.0% of checkable, the same as today's error
  rate). This is the largest lever and needs no XML.
- **Candidate-only k-types.** In V4, 6,763 cars (22.5%) stop at "provisional"
  although they pass the automatic threshold. 4,961 of them have one k-type.
  The cause is TecDoc promotion status: those k-types are candidate-only
  (engine ambiguous: 3,456 cars; displacement unresolved: 690; fuel unresolved:
  314). The AIS engine agrees with 4,469 of them and differs on 189 (4.1%).
  Resolving a candidate-only k-type **when the car's AIS engine code matches
  one of its engines** would add up to 14.9 points (about 960k cars).
- **Model from brand text.** 4,186 cars (14.0%) cannot be scored because no
  model is known. Taking the word after the make as the model resolves 571 and
  makes 793 provisional. But 71 of the 474 checkable resolved k-types (15%)
  differ from the AIS engine, **so this must go through reviewed model rules,
  never straight into matching.** Upper bound: about +1.9 points (about 123k).

Per manufacturer, resolved V0 → V4 (sample):

| Manufacturer | Cars | Today | V4 | Still unmatchable (no model) |
|---|---|---|---|---|
| Volvo | 6,331 | 36.5% | 49.0% | 18.5% |
| Volkswagen | 3,555 | 1.9% | 7.1% | 10.5% |
| Toyota | 1,981 | 21.3% | 49.2% | 2.1% |
| BMW | 1,655 | 1.6% | 10.5% | 26.0% |
| Kia | 1,490 | 7.0% | 30.8% | 2.6% |
| Mercedes-Benz | 1,395 | 15.6% | 30.8% | 33.0% |
| Ford | 1,372 | 3.9% | 12.5% | 18.0% |
| Škoda | 1,084 | 10.1% | 19.0% | 6.9% |
| Peugeot | 922 | 13.4% | 17.1% | 35.6% |
| Nissan | 628 | 2.7% | 40.3% | 5.4% |
| Mazda | 387 | 3.1% | 6.7% | 31.3% |

Škoda is not at 0 on live (v1's figure came from local rules). **Volkswagen is
the weak spot:** 1,020 of its unresolved cars stop at
`phonetic_candidate_requires_review`, which is a separate, VW-specific issue.

## 5. What still blocks after V4

| Reason | Cars in sample | Next lever |
|---|---|---|
| Provisional: candidate-only k-type | 6,733 | Step 5 (engine-confirmed candidate-only) |
| Two k-types too close (`candidate_margin_not_met`) | 4,286 | Month-level TecDoc years (step 7), drive type |
| No model (`model_evidence_missing`) | 4,186 | Step 6 (reviewed model rules, manufacturer type codes) |
| Context conflict still | 3,421 | Conflicts: kW 7,525, engine 7,437, body 6,988, year 5,953 |
| Phonetic model match needs review | 1,569 | VW investigation (1,020 of these) |
| Normalization review (tyre size, type approval) | 504 | Existing normalization rules |

## 6. Plan

Steps 1–3 need no XML and can start now. Each step is its own story branch and
PR with a before/after run of the same harness.

### Step 0: Baseline, harness and reference set

- Turn the analysis into a repeatable CLI (`match-impact-report`): a random
  seeded sample, the real evaluator, variants driven by flags, and a report of
  resolved / provisional / one-compatible / accuracy per variant.
- Build a reference set of 500–1,000 cars with a known correct k-type:
  reviewed matches; cars whose VIN embeds the engine (e.g. PSA `…9HZ…`); a
  hand-checked sample per top manufacturer.
- Pin every bulk match and the Vehicles matching screen to an explicit catalog
  batch. The newest-batch default can pick one of the ~100 test batches on live.

Done when the report reproduces V0 (16.0% ±0.5) and accuracy on the reference
set is known.

### Step 1: Engine-code comparison (matcher)

- In `ingestion/fuzzy_matching.py`, match engine codes exactly, then on
  TecDoc's bracket parts, then on the first token and hyphen head. The tolerant
  forms count as compatible with a smaller bonus than an exact match.
- Junk codes are ignored.
- A code unknown to the whole catalog is "unverified": never a conflict, never
  enough for automatic resolution (caps at provisional).
- Tests for each form, plus a regression on the 305,453-car format class.

Expected: +1.5 points with engine codes present (V2); accuracy improves.

### Step 2: Hybrid power

- Compare TS kW with the combustion engine's power for hybrids, not the
  system power.
- Use our own `ev_power_kw` (TS `ev_max_power`) as `power_kw` for electric
  cars. 310,137 EVs reach matching without power today; the XML kW for EVs is
  the same figure. Keep the existing ±2 kW tolerance; it already exists, so no new
  tolerance is needed.

Expected: about +1.5 points (+92k); gains agree with the AIS engine 473 of 475.

### Step 3: Body type

- Map the unmapped TS body codes (31,338 passenger cars have no body: 30,091
  with an empty code, then `93`, `99`, `95`, `SD`, `SB`).
- Fallback rule: when none of the plausible candidates matches the TS body,
  score without body and record `bodywork_unconfirmed` in the trace.
- Review the most frequent TS-body / TecDoc-body conflict pairs with the data
  owner and add model-level body rules where the TS code is systematically wrong
  (v1's example: Volvo V40 + `AF` = hatchback).

Expected: +9.7 points (+623k) at today's accuracy.

### Step 4: Import the XML as a normalization input

v1 proposed storing AIS values as rule resolutions. **Don't.**

- Rule values win over normalized values (`effective_value`), so AIS would
  overwrite TS instead of filling gaps.
- Rules are exported, imported and retired between environments, and
  retirement does not sync.
- It would create a second normalization path.

Instead:

- `staging.ais_vin_raw`: one row per VIN per extract, never edited; extract ID,
  export time and file checksum; a unique constraint on (extract, VIN) so a
  re-load is a no-op. Streamed loader (the file is 16 GB).
- A new normalization stage `ts.ais-enrichment`. It fills `engine_code`, model
  year, kerb weight, max weight, length and AIS status (deregistered, vehicle
  type, current plate), with every fill recorded in the trace (source `ais`, extract ID,
  original value). Conflicts become review reasons.
- A new extract supersedes the previous one on the next normalization run.
  Removing an extract and re-running removes its values.
- Pipeline version bump, golden corpus re-approved, integration tests: same
  extract twice is a no-op; a newer extract supersedes; TS values are never
  overwritten; junk is rejected.
- **Engine lookup table.** Within one TS make + variant + version the AIS
  engine code is the same for 98.1% of cars. Build a reviewed lookup table from
  it (feeding `ReviewedEngineFingerprintIndex`), so TS cars without an AIS row
  (future imports, new cars) also get an engine code.
- **Displacement derived from the engine code via TecDoc is display-only.**
  Feeding a TecDoc-derived value back into TecDoc matching counts the engine
  evidence twice.
- Colour is out of scope (no matching value).

### Step 5: Candidate-only k-types confirmed by engine code

- Allow automatic resolution of a candidate-only k-type only when the car's
  AIS engine code matches one of that k-type's engines (exact or bracket form),
  and nothing else conflicts.
- Map the "Petrol/Gas" and "Petrol/Alcohol/Gas" engine fuel labels in
  `reference_data.py` (still missing). That clears part of the
  `fuel_unresolved` group (2,002 k-types).
- Report the result by candidate-only reason (engine ambiguous / displacement /
  fuel).

Expected: up to +14.9 points (about 960k), measured against the reference set
before enabling.

### Step 6: Model from our own brand text (reviewed rules)

- Generate model rule proposals from the TS `brand` text: the word after the
  make, plus manufacturer type-code tables (old Volvo codes such as `745-883`,
  `13134`; Renault `B40705`, `D53Y05`, `JA`; Ford `PH2`, `DM2`).
- Proposals go through the existing reviewed rule workflow; nothing goes
  straight into matching (15% of naive extractions point to the wrong engine).
- Priority by volume: Volvo, Mercedes-Benz, Peugeot, Mazda, BMW, Saab.

Expected: up to +1.9 points.

### Step 7: Separate close pairs, VW

- Store TecDoc month ranges in the catalog. Then use the TS build month
  (already in TS for 81% of cars; the XML only copies it) to split k-types
  whose year ranges overlap.
- Investigate VW's `phonetic_candidate_requires_review` (1,020 of 3,555 VW
  cars in the sample).

## 7. Principles for writes to real data

Unchanged from v1 except where noted:

- Fill-only: AIS never overwrites a TS value. Disagreements go to review with
  both values visible.
- Field whitelist: engine code, model year, kerb weight, max weight, length,
  and AIS status (deregistered, vehicle type, plate). Never fuel, body,
  manufacturer or model. (v1 also listed colour and registration date; they are
  dropped from the first version. kW for EVs comes from our own `ev_power_kw`,
  not from AIS.)
- Provenance on every value; every AIS value is removable per extract.
- **Changed from v1:** AIS values enter through normalization, not through
  rule resolutions (see step 4).
- Deleted status never deletes anything.
- TecDoc rows are never modified by AIS data.

## 8. Decisions for stakeholders

These are observable behaviours, per the decision guide:

1. **Deleted vehicles (672,403 passenger cars):** hide by default, show with a
   label, or exclude only from matching statistics? v1's "mark the plate alias
   inactive" changes Alias behaviour and needs an owner and a Jira story.
2. **Different plates (5,983):** should the XML plate be searchable as a plate
   Alias of the same vehicle? Per the guide: lookup and reconcile first, and
   never create a second vehicle for a plate change.
3. **Body fallback:** is a match made without body type acceptable for
   automatic resolution? If it is, how should that be shown to users?
4. **Candidate-only k-types resolved by engine code:** is AIS engine
   confirmation enough evidence for automatic resolution, or should these stay
   provisional until spot-checked?
5. **New vehicles (660,840 active passenger cars registered since Dec 2023):**
   should they be added from the XML, with less data than TS cars (no
   variant/version, displacement or 4WD), or should we wait for a fresh TS
   extract? Either way: lookup and reconcile first, then mint.
6. **For the provider:** in addition to v1's questions (fuel codes, power
   unit, deletion flags, delivery rhythm), does a VIN missing from a later full
   file mean anything?

## 9. Risks and operations

- **The accuracy check is a proxy.** Step 0's reference set must exist before
  steps 3 and 5 are enabled on live.
- **Sampling:** figures are ±0.5 points. A full-population run takes about 90
  hours locally (20 cars/s with 3 workers). For stakeholder reporting, use a
  200k-car sample (±0.2 points, about 3 hours) or run it on the server.
- **Disk:** the local machine has about 20 GB free. The raw XML (16 GB) can be
  deleted once it is loaded into `staging.ais_vin_raw`; the extracted TSV is
  425 MB.
- **Licensed data:** neither the XML nor TecDoc files go into Git.
- **Sandbox:** `sandbox_step_xml` in the local database replaces v1's
  `sandbox_ais`. Remove with `DROP SCHEMA sandbox_step_xml CASCADE`.

## 10. Corrections to v1

| v1 | v2 (live data) |
|---|---|
| Compared set 541,489 vehicles | 6,444,864 passenger cars; 6,490,789 vehicles in the XML |
| Resolved today 7.9% | 16.0% (strict automatic) |
| Step 1 TecDoc rebuild still to do | Already live (`prod-v2-20260914`) |
| Škoda / Mazda 0 resolved | Škoda 10.1%, Mazda 3.1% |
| 360k duplicate TS rows | 5,914 |
| 47,800 deleted | 672,403 |
| 1,540 extra plates | 5,983 |
| ~40,000 junk engine codes | 277,528 |
| kW needs a tolerance | ±2 kW tolerance already exists; the issue is hybrids |
| XML engine code +50% resolved | Net 0 with today's matcher; value comes with steps 1 and 5 |
| Model extraction from the XML name | The XML name equals the TS brand text; use our own data |
