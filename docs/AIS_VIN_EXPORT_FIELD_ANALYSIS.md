# AIS VIN export: field-by-field value analysis

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Question | Is the AIS VIN export valuable for our vehicle data (built from TS normalization), and where exactly? |
| Our data | Full local copy of live: 6,532,590 vehicles (`core.vehicle_facts` + TS raw records) |
| XML | `exported-2026-09-19_11.05.36 testing.xml`: 10,814,705 VINs, exported 2026-09-19 |
| Compared | 6,403,668 passenger cars present in both (by VIN) |
| Related | [Enrichment plan v2](AIS_VIN_EXPORT_ENRICHMENT_PLAN.md) (matching impact) |

## 1. Verdict

**Yes, the XML is valuable, in four places.** For most other fields it is a
newer copy of the same Transportstyrelsen data we already have.

1. **It is 2¾ years newer than our TS data.** Our TS snapshot ends in early
   December 2023; the XML runs to 31 Aug 2026. It holds **660,840 active
   passenger cars we do not have at all** (almost all registered 2024–2026).
2. **Engine code**, which TS does not have. It covers 95.6% of our passenger
   cars, and it is determined by TS make + variant + version in 98.1% of
   cases, so it can become a reusable lookup table, not just a one-off fill.
3. **What changed since December 2023:**
   - 672,563 of our passenger cars are deregistered;
   - 12,650 were converted to tractors (A-traktor), trucks or buses;
   - 5,983 have a new plate;
   - 29,486 have a new colour;
   - 72,521 have been registered since.
4. **Attributes our TS extract lacks:** kerb weight, max weight and length for
   about 99% of cars, and a genuine model year (TS has one for only 16%).

Where the two overlap, the XML agrees with our TS data on 99.4–100% of cars.
The differences are explained by changes since 2023, not errors, so the file
is trustworthy there. The quality problems the provider warned about are
concentrated in the engine code: 277,528 junk values (4.5%).

## 2. Field by field (6,403,668 passenger cars in both)

"Agree" and "differ" count cars where both sides have a value. "XML fills"
counts cars where our TS value is empty and the XML has one.

| XML field | TS has | XML has | Agree | Differ | XML fills | What it is | Value |
|---|---|---|---|---|---|---|---|
| **Engine code** | 0% | 96.2% | – | – | **6,161,021** (5,883,493 after junk filter) | AIS data; not in TS | **High**: matching evidence (see plan) |
| **Kerb weight** | 0% | 99.8% | – | – | **6,393,328** | TS field our extract lacks | **Medium**: display, search, vehicle-type checks |
| **Max weight** | 0% | 98.9% | – | – | **6,335,794** | TS field our extract lacks | **Medium**: same; > 3,500 kg means not a passenger car |
| **Length** | 0% | 99.8% | – | – | **6,393,217** | TS field our extract lacks | Low–medium: display |
| **Model year** | 15.6% | 100% | 99.1% | 8,655 | **5,406,003** | Real model year: +1 for cars built Aug–Dec, matches the VIN year code 66–71% | **Medium**: display and search; the matcher uses production year |
| **Deregistered** (`Import_Deleted`) | 2,387 cars | 10.5% | – | – | **670,176** (672,563 flagged in total) | Deregistered since Dec 2023 (scrapped or exported; only 513 say "Skrotad") | **High**: status |
| **Vehicle type** | 100% | 100% | 99.8% | **12,652** | 0 | PB→TR 11,580 (A-traktor), PB→LB 1,044, PB→BUSS 26 | **High** for vehicle-type correctness |
| **Plate** | 100% | 100% | 99.9% | **5,983** | 0 | Plate changes. 4,616 of the new plates belong to another car in our data | **High** for plate lookups (small volume) |
| **Registration date** | 98.5% | 99.6% | 99.9% | 5,146 | **72,521** | Fills are cars registered 2024–2026; differences are old-car corrections | Medium |
| **Colour** | 100% | 100% | 99.5% | 29,486 | 2,512 | Mostly "OKÄND" (unknown) → a real colour, and metallic refinements | Low: display |
| kW | 94.9% | 99.8% | 99.9% | 5,807 | 313,443 | 310,137 of the fills are EVs, and TS already has their power (see §4) | Low from the XML |
| Fuel / fuel 2 | 99.8% / 9.9% | same | 100% | 823 / 13 | 3,298 / 1,553 | Same TS codes; 153 petrol→ethanol conversions | None (confirm only) |
| Gearbox | 90.9% | 91.0% | 100% | 1,125 | 5,252 | Same TS codes | None |
| Body code | 99.6% | 99.6% | 99.8% | 14,666 | 4,216 | Same TS codes; the differences are the A-traktor conversions (→ `07`) | None beyond the vehicle-type change |
| Group / make code | 95.0% / 100% | same | 99.4% | ~38k | 1,153 | Same as TS `group_no` with a make prefix; the differences are AIS placeholders (`VO000000` on non-Volvos) | None; TS is better |
| Month of manufacture | 81.4% | 81.4% | 100% | 128 | 1,193 | Copy of the TS build month | None |
| Tyre size | 99.7% | 99.8% | 95.1% | 312,050 | 6,144 | The XML holds one tyre; 304,877 "differences" are the TS **rear** tyre, which we have | None |
| Name of the car | 100% | 100% | – | – | – | TS `brand` + TS `model` joined (4,021,691 of 4,037,592) | None |
| Chassis number | – | 100% | – | – | – | Always equal to the VIN | None |
| Power unit (EG / DIN / SAE) | – | 99.4% | – | – | – | Standard of the kW figure; does not explain kW conflicts in matching | Low |
| AIS type reference | – | 97.3% | – | – | – | Groups of identical vehicles | Out of scope (provider: not reliable) |
| Remark | – | 8.9% | – | – | – | Codes `1`, `5`, `3` | Unknown; ask the provider |
| Last updated | – | 1,366 cars | – | – | – | – | None |

## 3. Where exactly it is valuable

### 3.1 Coverage and recency (largest)

| First registered | In our TS | Active passenger cars in the XML, missing from ours |
|---|---|---|
| Nov 2023 | 25,848 | – |
| Dec 2023 | 2,860 | – |
| 2024 | 14,485 | 204,679 |
| 2025 | 37,114 | 223,084 |
| 2026 | – | 179,888 |
| No date | – | 44,588 |
| **Total missing** | | **660,840** |

For these cars the XML has:

| Field | Coverage |
|---|---|
| kW, fuel, model year | 100% |
| Body | 99.8% |
| Model (inside the name) | 99.6% |
| Month of manufacture | 97.1% |
| Registration date | 93.3% |
| Gearbox | 88.3% |
| Engine code | 77.4% |

It lacks what only TS has: variant/version, type approval, displacement, 4WD,
EU category and emission class. So they would match less well than TS cars.

Top makes: Volvo 120k, VW 78k, Toyota 55k, Kia 52k, Mercedes 39k, Škoda 35k,
BMW 35k, Tesla 34k.

**What this means:** our TS data is stale. Plate lookups for about 660k
current cars fail today. The durable fix is a fresh TS extract. The XML can
bridge until then, or be used every time as the change feed.

### 3.2 Engine code

- 6,161,021 passenger cars (95.6%) get an engine code, 5,883,493 after
  removing junk. 5,535,425 match a TecDoc engine code exactly and 305,453 match
  once TecDoc's bracket part is ignored.
- **It is type-level data.** Within one TS make + variant + version, the
  XML engine code is the same for 98.1% of cars. 4,960,868 cars sit in groups
  where ≥ 95% share one code (70,758 variant/version groups in total).
- So the XML can seed an **engine-code lookup table**: one row per TS make +
  variant + version, giving the engine code those cars share. For example,
  Volvo `DZA5` / `DZA5C6??` → `D5244T21` (24,349 cars, 99.5% agree).
  - 70,758 rows in total. 34,577 are confident (≥ 5 cars, ≥ 95% agree).
  - Only confident rows are used. Placeholder codes are skipped: Nissan `A` /
    `A01` covers five different engines. Rows such as Kia `C5P21` / `D61AY1`
    (81% `G4LE`) go to review.
- What the table adds:
  - **Future cars:** a car in a later TS import whose variant/version is
    already in the table gets its engine code without needing AIS. This is the
    main reason to build it.
  - **Today:** it fills 31,954 cars where the XML code is missing or junk.
  - **Quality check:** it flags 2,738 cars whose XML code disagrees with a
    confident row. These are likely AIS errors.
- The matcher already has a hook for this kind of table
  (`ReviewedEngineFingerprintIndex`, used when a car has no engine code). The
  rows would be loaded as reviewed fingerprint rules.
- 647,773 cars have no variant/version in TS (mostly older cars). For them,
  the per-VIN XML value is the only source.

### 3.3 Changes since December 2023

| Change | Cars | Effect in our data |
|---|---|---|
| Deregistered (scrapped or exported) | 672,563 | Still shown as current vehicles |
| Converted PB → TR (A-traktor) | 11,580 | Wrongly treated as passenger cars |
| Converted PB → LB (truck) / BUSS | 1,044 / 26 | Same |
| Plate changed | 5,983 | 4,616 of the new plates point to a different car in our data, so lookup by those plates returns the wrong car |
| Newly registered (registration date filled) | 72,521 | Missing registration date |
| Colour changed | 29,486 | Old colour shown |
| Petrol → ethanol conversions | 153 | Old fuel |

### 3.4 New attributes

Kerb weight (6.39M), max weight (6.34M), length (6.39M) and model year
(5.41M fills) are new for us. They are display and search attributes. Max
weight is also a check for the vehicle-type work: a passenger car cannot
exceed 3,500 kg.

## 4. Things that looked like XML value but are our own data

- **EV power (310,137 cars).** TS already has it: the XML kW equals TS
  `ev_max_power` / 10, which our normalization stores as `ev_power_kw`. It is
  just not used as `power_kw`, so electric cars reach matching without power.
  An own-data normalization fix.
- **Model text (155,927 cars).** TS has the model name in its `model` field,
  but our normalization leaves `model_family` empty. An own-data rule gap.
- **Month of manufacture.** TS has it (build month) for 81%. The XML is a copy.

## 5. What to update in our vehicle data

| Update | Cars | Kind | Needs |
|---|---|---|---|
| Engine code (clean) | 5,883,493 | Fill (TS has none) | Normalization stage + engine lookup table |
| Deregistered status | 672,563 | New status field | Stakeholder decision on visibility |
| Vehicle type (A-traktor, truck, bus) | 12,650 | Correction of newer state | Feeds `vehicle_scope` |
| Plate changes | 5,983 | New plate Alias; old plate kept as history | Decision guide: lookup, reconcile, never mint a second vehicle |
| Model year | 5,406,003 (+8,655 disagreements to review) | Fill | Display and search |
| Kerb weight, max weight, length | ~6.39M each | New fields | Display and search |
| Registration date | 72,521 | Fill | – |
| Colour | 29,486 (+2,512 fills) | Newer value | Optional |
| New vehicles | 660,840 | New records | Identity decision; better, a fresh TS extract |
| EV power into `power_kw` | 310,137 | Own data (`ev_power_kw`) | Normalization fix, no XML |
| Model family from TS model text | 155,927 | Own data | Reviewed rules, no XML |
| Fuel, gearbox, body, group code, month, tyre, name, chassis, AIS type, remark | – | Ignore | – |

## 6. Questions for the provider

1. `Import_Deleted` versus `MarkedForDeletion` / `_temp` / `_calc`: which one
   means deregistered, and can scrapped be told apart from exported?
2. Remark codes `1`, `3`, `5`.
3. Where the engine code comes from (type/variant tables or per-VIN
   decoding), and how model year is derived.
4. Delivery rhythm (full file or changes), and whether a VIN missing from a
   later file means anything.

## 7. Reproducing

Local sandbox schema `sandbox_step_xml` (remove with
`DROP SCHEMA sandbox_step_xml CASCADE`):

| Table | Contents |
|---|---|
| `vin_export` | 22 XML fields |
| `vin_extra` | Tyre, length, max weight, colour, remark, flags |
| `ts_extra` | TS fields not in `vehicle_facts` |
| `cmp` | One row per vehicle, TS vs XML |
