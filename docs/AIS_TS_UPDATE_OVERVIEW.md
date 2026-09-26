# AIS → TS update: what we will add and change

| Field | Value |
|---|---|
| Status | Overview for approval, nothing implemented |
| Date | 2026-09-26 |
| Source | AIS VIN export `exported-2026-09-19_11.05.36 testing.xml` (10,814,705 VINs, exported 2026-09-19) |
| Our data | Full local copy of live: TS snapshot from early Dec 2023, 6,532,590 vehicles |
| Related | [Field-by-field analysis](AIS_VIN_EXPORT_FIELD_ANALYSIS.md), [Enrichment plan v2](AIS_VIN_EXPORT_ENRICHMENT_PLAN.md) |

All counts below are exact, measured on the local copy. Nothing has been
written to real tables.

## 1. Approach

- **No AIS table.** The AIS file updates our existing TS data and is not
  stored separately.
- **Existing cars:** their current TS record is updated **in place**:
  - The record keeps its ID, so its normalization results, rule results and
    matches stay attached.
  - The previous value of every changed field is kept **inside the same
    record**, together with the AIS extract date, so every change can be seen
    and undone.
- **New cars:** they become new records in the same TS table. The AIS extract
  is their source, and fields only TS has are filled by rules where possible
  (§5).
- **Engine codes, and other values TS never had, come from reviewed rules**
  kept in our existing rule store. Normalization applies them and records the
  rule in the trace.
- **Afterwards:** normalization re-runs, `vehicle_facts` is refreshed (new
  columns, no new tables) and matching re-runs. The Vehicles tab, search and
  the matcher all keep reading the same vehicle record.

## 2. Existing cars: registry values that change

6,490,790 of our TS records are in the AIS file. For these fields the AIS
value is the newer state of the same Transportstyrelsen data (Sep 2026 vs
Dec 2023).

| Field | Records | What happened since Dec 2023 | Effect |
|---|---|---|---|
| Deregistered (new status) | **674,160** | Scrapped or exported (672,563 of them passenger cars) | Marked deregistered, never deleted |
| Registration date | 78,856 | 73,694 registered since; 5,162 corrections on old cars | Updated |
| Colour | 32,683 | Repaints, and "OKÄND" (unknown) → a real colour | Updated |
| Body code | 19,157 | Mostly conversions to A-traktor (→ `07`) | Updated |
| Vehicle type | **12,664** | PB→TR (A-traktor) 11,580, PB→LB 1,044, PB→BUSS 26 | Updated; the car leaves "passenger" |
| Gearbox | 8,129 | 1,156 changes, 6,973 fills | Updated |
| Plate | **6,007** | Plate changes. 4,616 of the new plates are on another car in our data today | Updated; the old plate is kept as history |
| kW | 5,823 changes, 3,327 fills | Engine swaps, conversions, corrections (EVs not included, see §6) | Updated |
| Fuel | 4,152, plus 1,553 second-fuel fills | E.g. 153 petrol→ethanol conversions | Updated |
| Build month | 2,286 fills | – | Filled |
| **Records with at least one changed value** | **778,511** | | |

**Not taken from AIS:**

| Field | Why |
|---|---|
| Group code | AIS placeholders, e.g. `VO000000` on non-Volvos |
| Tyre | AIS holds one tyre; its "differences" are our rear tyre |
| Name of the car | Our brand + model text joined |
| Model year where TS already has one | 9,128 disagree; TS kept, sent to review |

## 3. Existing cars: values TS never had

| Value | Records | How it arrives |
|---|---|---|
| Engine code | **5,958,004** (junk codes removed) | The car's own AIS value; engine rules (§4) as fallback |
| Model year | 5,471,762 fills | The car's own AIS value (TS keeps its own where present) |
| Kerb weight | 6,480,315 | The car's own AIS value |
| Max weight | 6,422,773 | The car's own AIS value |
| Length | 6,480,199 | The car's own AIS value |

## 4. Rules: values TS lacks, learned from AIS

A rule says "every car with this TS key has this value". It is kept only
when **at least 5 cars share the key and at least 95% agree**. "Agree" is
measured against the cars' own AIS values. "Unseen cars" means rules learned
from cars registered before 2022, tested on cars registered 2022–2023.

| # | Rule (TS key → value) | Rules | Passenger cars covered | Agree | Unseen cars |
|---|---|---|---|---|---|
| R1 | make + variant + version → **engine code** | 34,577 | 4,901,710 (83%) | 99.94% | 98.4% correct |
| R2 | make + group code → **engine code** | 37,611 | 5,021,159 (85%) | 99.88% | 97.1% correct |
| R3 | make + type + displacement + kW + fuel → **engine code** (older cars without variant/version) | 4,946 | 3,167,735 (54%) | 99.84% | – |
| | **R1 → R2 → R3 chained** | 77,134 | **5,596,777 (87%)** | **99.87%** | 96.8% correct, 66.5% covered |
| R4 | make + vehicle year + build month → **model year** | 8,563 | 4,313,415 (67%) | 99.72% | – |
| R5 | make + variant + version → **max weight** | 34,230 | 4,668,726 (74%) | 99.94% | – |
| R6 | make + variant + version → **length** | 21,737 | 2,762,483 (43%) | 99.63% | – |

What the chained engine rules do:

- They reproduce the car's own AIS engine code for 5,558,717 cars and
  disagree for 7,230 (flagged for review).
- They fill **30,830** cars where AIS has no usable code.
- **317,546** cars have an AIS code but no rule. That is the difference
  between "rules only" and "rules + the car's own value" (decision A in §8).
- **Their lasting value is future cars.** On unseen cars they cover about
  two thirds, at about 97% correct. So a value filled by a rule is marked as
  rule-derived and never overrides a real value.

**Not suitable as a rule:**

- **Kerb weight** is consistent for only 28–38% of groups, because it varies
  per car with equipment. It stays per-car only.
- **Deregistration, plate, vehicle type and colour** are facts about one car,
  not patterns. They are updated per car (§2).

## 5. New cars: 660,840 active passenger cars we don't have

Almost all were registered 2024–2026 (2024: 204,679; 2025: 223,084;
2026: 179,888). Each becomes a new TS record built from:

**Taken directly from AIS**

| Value | New cars covered |
|---|---|
| VIN, plate, fuel, kW, model year | 100% |
| Body code | 99.8% |
| Build month | 97.1% |
| Registration date | 93.3% |
| Gearbox | 88.3% |
| Engine code | 77.4% (156,033 without one, of which 99,523 are electric) |
| Colour, one tyre size, weights, length | – |

**Filled by rules learned from our own TS data**, keyed by make + group code.
82% of new cars (541,183) have a group code we already know.

| # | TS field the new car lacks | Rules | New cars reached | Agree |
|---|---|---|---|---|
| R7 | EU category | 45,771 | 524,712 (79%) | 99.96% |
| R8 | Brand text | 35,594 | 493,566 (75%) | 99.75% |
| R9 | Model text | 18,067 | 467,885 (71%) | 99.96% |
| R10 | Type code | 27,275 | 464,748 (70%) | 99.95% |
| R11 | 4WD flag | 45,292 | 463,092 (70%) | 99.94% |
| R12 | Variant | 27,859 | 371,366 (56%) | 99.92% |
| R13 | Displacement | 26,624 | 285,846 (43%) | 99.97% |

The engine rule R2 adds a code for 11,649 new cars that have none from AIS.

**What new cars will never have** (only TS provides it): version, type
approval, emission and Euro class, CO2, wheelbase, passengers. They normalize
and match somewhat less completely than TS cars.

**Not added:**

- 1,002,722 active trucks (LB) and 19,596 buses. They are outside our TS
  scope.
- 2,191,183 passenger cars already deregistered before we would have seen
  them.

**Checked before adding:** 2,285 new VINs carry a plate that another car in
our data uses. Per the decision guide, look up and reconcile first; never
mint a second vehicle for the same car.

## 6. Found along the way: rules from our own data (no AIS needed)

| # | Rule | Cars | Note |
|---|---|---|---|
| R14 | Electric cars: `power_kw` = our own `ev_power_kw` (TS EV max power) | 310,137 | The AIS kW for EVs is the same figure |
| R15 | Model family from TS model text | 155,927 | TS has the model name; our normalization leaves the family empty. Reviewed rules |

## 7. Where the rules live and how they're reviewed

- **Store:** our existing rule store, as versioned rule rows next to the
  translation rules (`core.translation_rule_definitions`). The table is
  already defined in code; its migration has not run on live yet. The
  matcher's existing "engine fingerprint" rule type (make + variant/version →
  engine code) is the same idea as R1.
- **Needed extension:** today a rule matches one term in one field. R1–R6
  and R7–R13 need keys of two or more fields (e.g. variant + version).
- **Volume:** about 368,000 rules if R1–R13 are all adopted, against about
  900 rule entries today. Nobody can review that many one by one, so:
  - rules that meet the threshold are accepted in bulk, per rule group, after
    a spot-checked sample;
  - mixed or placeholder keys are skipped (e.g. Nissan `A` / `A01` covers five
    engines);
  - disagreements (e.g. the 7,230 engine cases) go to review.
- **Traceability:** every value a rule fills carries the rule ID and its
  version in the normalization trace. Retiring a rule removes its values on
  the next normalization run.

## 8. Decisions needed before building

| # | Decision | Options | Recommendation |
|---|---|---|---|
| A | Engine code: rules only, or rules + each car's own AIS value? | Rules only: 5.60M cars. With own values: 5.96M (+317,546) | Own value first, rules as fallback and for future cars |
| B | Deregistered cars (674,160): how are they shown? | Hidden by default / shown with a label / excluded only from counts | Labelled, with a filter (like vehicle type) |
| C | New cars: passenger only? | Passenger (660,840) / also trucks and buses | Passenger only |
| D | Plate changes (6,007): is the old plate still searchable? | Yes as history / no | Yes as history, pointing to the same car |
| E | Rule families to adopt now | R1–R3 engine, R4 model year, R5–R6 weight/length, R7–R13 new-car completion, R14–R15 own data | R1–R3, R7–R13, R14 first; R4–R6 and R15 after |

## 9. Risks

- **Rule accuracy on future cars is about 97%, not 99.9%.** Rule-filled
  values are marked, never override real values, and are re-checked against
  the next AIS file.
- **The meaning of `Import_Deleted`** (scrapped vs exported vs off the road)
  must be confirmed by the provider before it is shown to users.
- **Dedupe is per plate.** `vehicle_facts` keeps one row per plate. Plate
  changes and new cars must go through the same dedupe, which needs a test
  for plate moves.
- **In-place updates change TS values that rules and reviews were based on.**
  The 182 TS rules and their 2.97M results must be re-checked after the
  update (the rule results stay attached; their conditions may no longer
  match).
- **Work volume:** every one of the 6.49M records gets new values, so this is
  a full re-normalization and projection refresh, not a small backfill.
