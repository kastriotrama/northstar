# Bodywork (body type) corrections — 2026-09-26

Every passenger car in `core.vehicle_facts` (local restore of live, 6,444,864 cars) was checked
against the TecDoc catalogue (`tecdoc-0326-canonical-full-prod-v2-20260914`). Where TecDoc lists
several bodies for a model, the car's own length from the AIS export
(`sandbox_step_xml.rule_base`, validation only) was used to see which one it really is.

Rules and tables:

| File | Content |
|---|---|
| `outputs/proposed/bodywork/bodywork_rules_2026-09-26.json` | 4,401 rules in `core.match_resolution_rules` shape, plus the opt-in crossover rules, held-back rules and the rule to retire |
| `outputs/proposed/bodywork/bodywork_rules_2026-09-26.csv` | the same rules, one row each, largest first |
| `outputs/proposed/bodywork/bodywork_decisions_by_model_2026-09-26.csv` | per model: cars, cars changed, from → to, TecDoc bodies |

**Status:** applied to the local database on 2026-09-27, including `core.vehicles`. Live was not
touched. See *Applied to `core.vehicles`* below for what changed, what the audit after applying
found and how to undo it.

## Applied to `core.vehicles` — 2026-09-27

Applied to the local restore (`northstar-tecdoc-repromote-postgres-1`) in this order:

1. Retired the over-broad Volvo rule `31614f07` (174,151 TS rows went back to their registry value).
2. Applied the rules through the rule service, in precedence order: the same validation and runner as the TS data
   screen. Each batch writes three things in one transaction: the TS projection, the resolution ledger and the
   linked NorthStar vehicles.
3. **AIS-only vehicles** have no TS record. They got the same rules as review observations carrying the same rule id,
   on 416,338 vehicles. Retiring a rule removes it there too.
4. An audit after applying found 37 wrong rules. They were retired and replaced by 79 narrow rules (below).

| | Count |
|---|---:|
| Correction rules active | 5,048 (4,969 from this proposal + 79 replacements) |
| Correction rules retired | 37 (+ the 2026-09-21 Volvo rule) |
| TS records carrying a correction | 2,518,331 |
| NorthStar vehicles carrying a correction | 2,934,027 (2,517,689 from TS + 416,338 AIS-only) |

The applied set is larger than the 2,342,644 proposed for two reasons:
- retiring the Volvo rule first returned about 174k cars to their registry value, and the new rules then corrected them
  under their own rule ids;
- the AIS-only vehicles come on top.

Passenger body types in `core.vehicles` (7,088,580 vehicles). "Before" is after the Volvo rule was retired and before
any new rule:

| Body | Before | After |
|---|---:|---:|
| estate | 3,371,227 | 2,153,644 |
| suv | 10,738 | 1,691,749 |
| hatchback | 1,087,140 | 1,678,175 |
| sedan | 435,055 | 545,784 |
| covered_body (closed, body unknown) | 747,433 | 391,107 |
| multi_purpose_vehicle | 1,043,943 | 314,421 |
| convertible | 81,879 | 134,644 |
| coupe | 63,541 | 122,277 |
| open_body (open, body unknown) | 77,292 | 24,899 |
| empty | 160,457 | 21,798 |
| motorhome, pickup, cargo_estate, other | 9,875 | 10,082 |

### Audit after applying

Every corrected row was checked against:
- its own registry text;
- its length;
- other registrations with the same text;
- its NorthStar scope.

| Problem found | Rules retired (TS rows) | What replaced them |
|---|---:|---|
| Rules keyed only on a variant code that other models share:<br>- Opel AA11: Antara and Combo Tour → hatchback<br>- Mazda F91: Mazda6 estates → suv<br>- Fiat AXA1A: Doblò → hatchback<br>- Maserati AB: Coupé / GranSport → sedan<br>- Cadillac ABAA: SRX → sedan<br>- MINI: Clubman estates → hatchback | 8 (876) | - Antara → suv<br>- Combo Tour → MPV<br>- SRX → suv<br>- MINI hatch rule re-created without the Clubman variants<br>- the rest back to the registry value |
| Rapido S80 motorhomes that the normalizer files as Mercedes S-Class → sedan | 1 (9) | back to the registry value (MPV) |
| "D-4D" (Toyota diesel) read as "4-door" → sedan | 6 (628) | Corolla "SEDAN / 4D" rule re-created without the D-4D texts |
| Fiat 127 "Combi" (the 3-door hatch, 3.65 m) → estate | 1 (217) | → hatchback |
| Classic cars where TecDoc lists one wrong body:<br>- MG Midget / TC / TF as saloons<br>- Firebird and Eldorado as saloons<br>- Triumph Herald as an estate<br>- fills that guessed coupe for MGB / MGA / SL / Jensen-Healey | 21 (592) | - roadsters → convertible<br>- closed-coded Firebird / Eldorado → coupe<br>- the rest follows its registry text |
| Explicit body words the rules had overridden:<br>- COUPÉ / 2DR HT: Coupe de Ville, Impala, Skylark, Le Mans, Škoda 130 G, Audi 80/90 Coupé, 404 Coupé<br>- CAB / CONV / ROADSTER: Buick, Mustang, Camaro, Saab 9-3<br>- Swedish "kombisedan" (5-door hatch)<br>- "CLIO 1,2" (1.2 litre) read as Renault 12<br>- Kia Sephia HB<br>- Volvo P1800 ES<br>- Corsa A TR / 4D sedans (4.0 m) | — | replacement rules follow the word; 2,618 rows across all 79 replacements |

Two data repairs on `core.vehicles`:
- **214 vehicles were left empty when the Volvo rule was retired.** They had no registry value stored behind the
  review to fall back to. They were refilled from their TS record.
- **131,102 corrected vehicles had the same gap.** Their registry value is now stored behind the correction, so
  retiring a rule restores it.
- The cause is in `ingestion/vehicle_core_ts.process_ts_page`: `{**observations, **review_observations}` drops the TS
  value of a reviewed field. It needs a code fix and a regression test (not part of this change).

Checks after the repairs:
- 0 review markers point at a retired rule.
- Every TS-linked passenger vehicle agrees with its TS projection except:
  - 2,126 where a newer AIS value wins, as before this work;
  - 3 vehicles linked to a second TS record that was corrected.
- 17 motorhomes still carry a correction, consistent with how most motorhomes are recorded:
  - Defender / Land Cruiser → suv;
  - Transit, Sprinter, Master, Multivan, Primastar → MPV.
- 2,759 records are passenger cars in the TS projection but `other`, `test_record` or `goods` in `core.vehicles` under
  the new scope classification. They got the same correction as their TS record.

### How to undo

- Every rule is by `Claude (for Valon Shabani)`. Its note starts with `Bodywork correction 2026-09-27 BDY-…`
  (this proposal) or `… FIX-…` (audit replacements), followed by the group, the gray-zone alternative where there is
  one, and a link to this file.
- Retiring a rule (TS data screen, or `MatchReviewService.retire_resolution_rule`) does three things:
  - returns its TS rows to the registry value;
  - takes its value back off every NorthStar vehicle, TS-linked and AIS-only;
  - those vehicles fall back to what they said before.
- A snapshot from before applying was kept outside the repo, in the session scratchpad: the TS overlay, the active
  resolutions and the body type of every vehicle.

## Result

| | Cars | Share |
|---|---:|---:|
| Passenger cars checked | 6,444,864 | 100% |
| **Body type corrected by a rule** | **2,342,644** | **36.3%** |
| … clear registry errors | 2,216,975 | 34.4% |
| … gray zone, aligned to TecDoc (decision below) | 125,669 | 1.9% |
| … of which filled where the body was empty | 8,485 | |
| Already consistent with TecDoc / the registry text | 3,442,011 | 53.4% |
| Model not identifiable (no model text, family or usable type code) | 430,867 | 6.7% |
| Old national code, TecDoc lists several bodies, nothing decides | 174,968 | 2.7% |
| No TecDoc body for the model/year | 53,747 | 0.8% |
| Held back (the model's own evidence disagreed) | 563 | |

Largest corrections:

| From → to | Cars |
|---|---:|
| estate → suv | 685,992 |
| multi_purpose_vehicle → suv | 333,949 |
| multi_purpose_vehicle → hatchback | 280,484 |
| estate → hatchback | 266,159 |
| covered_body → sedan | 189,045 |
| covered_body → hatchback | 109,445 |
| hatchback → suv | 97,585 |
| multi_purpose_vehicle → estate | 92,292 |
| estate → multi_purpose_vehicle | 70,350 |
| open_body → convertible | 51,111 |
| sedan → hatchback | 46,274 |
| covered_body → coupe | 42,369 |

## Why the registry body type is wrong

The registry copies the body code the manufacturer declared on the certificate of conformity
(EU codes AA sedan, AB hatchback, AC estate, AD coupe, AE convertible, AF multi-purpose), or an
older Swedish code (01 closed, 02 open, 03 kombi, 04/05 with sunroof, 06/07 taxi). Five things go
wrong:

1. **There is no SUV code.** SUVs are declared AC (estate), AF (MPV), AB or even AA:
   - Tiguan 64,454 → suv
   - Qashqai 49,606 AF + 3,941 AC → suv
   - RAV4 30,143 AC + 19,284 AF → suv
   - Niro 48,125 AC → suv
   - XC60 36,620 AC still left after the earlier reviewer rules → suv
   - XC90 28,655 AF → suv
   - ID.4, Model Y, Kuga, Sportage, C-HR (AB), X1/X3 (AC; X3/X4/X6 also AA)
   - …about 300 more models.
2. **Some manufacturers declare AF for ordinary cars.** These are correct as hatchback or estate:
   - Toyota Yaris (55,867), Auris (21,710 hatch + 13,772 Touring Sports), Avensis T27 Tourer (23,694)
   - Kia Picanto / Rio / Ceed-era, Hyundai i10/i20/i30
   - Nissan Leaf/Micra/Pulsar, Peugeot 308, SEAT Ibiza, Opel Astra/Corsa, Volvo V40 (40,803)
   - Volvo V50 estate (23,818).
3. **One code is used for two bodies.** The type code or variant separates them, and length confirms it:
   - **VW Golf:** every modern Golf is AC. The type code splits hatchback (1K / AU / CD, 4,175–4,275 mm) from
     Variant (1KM / AUV / CDV, 4,525–4,635 mm): **100,603 hatchbacks registered as estates.**
   - Audi A3 Sportback (AC, 9,498)
   - BMW 1 Series (AC, 24,403); 2 Active/Gran Tourer (AC → MPV, 6,127); 3/5 Gran Turismo (AA → hatchback, 8,127)
   - Mercedes A-Class (AC, 20,497)
   - SEAT Leon hatch (AC; variant `B…` vs `X…/F…`)
   - MINI 5-door (AC)
   - Toyota, whose variant letter `(H)`/`(W)` = hatch/wagon.
4. **The old national codes are vague or misleading:**
   - 01 "closed" says only "not a kombi". It is resolved when TecDoc has one closed body, from the model text
     (e.g. Volvo 244 vs 245), or for makes proven to use it that way. Measured with length:
     - VW 0.1% estates, Renault 0%, Peugeot 2%, Citroën 2.7%, Volvo 0.1–1.7% (the 850 excepted at 7.4%);
     - Ford 18%, Škoda 23%, Chevrolet 20% and Subaru 29% registered estates as 01, so those stay unresolved
       unless their own type code decides (Ford Focus DNW, Mondeo BWY).
   - 03 "kombi" was also used for hatchbacks (Saab 900/9000, Peugeot 205, Polo, Starlet, Colt). Today it
     always becomes estate.
   - 02 "open" becomes convertible, or suv for an open off-roader.
5. **The model family holds another model:**
   - Corolla Cross under "Corolla" (type XG1TJ, 3,558)
   - C5 Aircross under "C5" (2,910)
   - Scénic III under "Megane" (1,959)
   - Countryman under "MINI"
   - S-Cross under "SX4"
   - Outback registered as "Legacy" (kept as estate).

Two earlier reviewer rules also need attention (see *Before applying*).

## The rules

Each rule has the same shape as `core.match_resolution_rules.conditions`. It combines:
- the model: `normalized.model_family`, or the exact registry brand/model/variant text where no family
  exists;
- optionally a production-year band and the manufacturer's type code or variant prefix;
- **the current wrong values** (`normalized.bodywork_form equals [...]`).

It sets `bodywork_form` with `override=true`. The 886 fill rules use `override=false` and only fill
empty values. Because every rule names the values it corrects, a car that is already right or has a
deliberate special body (motorhome 08, cargo estate AG, taxi-/police-only codes) is never touched.

The normalizer files coachbuilt campers under Mercedes B/S/G-Class (Hymer "B780ML", Rapido "S80",
Pilote "G743"), and `body_code2` SA marks them. The file's rules carry `source.body_code2 not_equals [SA]`.
The rule API does not accept `body_code2`, so the applied rules exclude those camper model texts
instead (`source.model not_equals [...]`). That missed the Rapido "S80"; see the audit above.

| Rule group | Rules (of which fill) | Cars decided | How it was decided |
|---|---:|---:|---|
| TecDoc has one body for the model and year | 2,577 (672) | 1,564,838 | the registry value contradicts it |
| Reviewed discriminators (type code / variant / year) | 102 | 441,498 | checked by hand, validated by length |
| National codes 01 / 02 / 03 | 690 | 165,638 | rules above |
| Explicit body word in the registry text | 882 (203) | 136,703 | e.g. "SPORTCOMBI", "4D", "COUPÉ", Volvo 744/745 |
| TecDoc disputed (see below) | 31 (11) | 23,948 | |
| M1 people carriers TecDoc files as "Bus" | 65 | 9,838 | Multivan, V-Class, Vivaro … → multi_purpose_vehicle |
| Undo of the wrong 2026-09-21 Volvo override | 54 | 181 | only needed if that rule is not retired |
| **Total** | **4,401 (886)** | **2,342,644** | |

When rules overlap, they agree except for 153 cars. For those, explicit registry text beats a reviewed
discriminator, which beats an automatic rule.

## Where TecDoc is not followed

TecDoc is the default authority. It is overruled only where its label is plainly wrong for the model:

| Model | TecDoc | Rule sets | Cars |
|---|---|---|---:|
| Volvo PV 444 / PV 544 | Saloon / **Hatchback** | sedan (2-door fastback with a boot lid) | 11,473 |
| Saab 96 (+ Monte Carlo) | **Hatchback** | sedan | 6,672 |
| Tesla Model X | **Hatchback** | suv | 2,771 |
| Ford Fusion (EU, JU) | **Estate** | hatchback | 2,483 |
| Saab Sonett | **Saloon** | coupe | 405 |
| MG TD | **Saloon** | convertible | 128 |
| Alfa Romeo Brera | **Hatchback** | coupe | 16 |
| Hyundai IONIQ 6 | **Hatchback** | sedan (the registry's AA is kept) | 0 |

The registry value is also **kept** where TecDoc is simply missing the body:
- Volvo P1800 ES estate;
- Subaru Outback registered as Legacy;
- classic (pre-1990) coupés, convertibles and wagons that TecDoc lists only as saloons (Impala,
  Eldorado, Skylark …);
- any registry convertible that TecDoc lists only as a closed body (Camaro, Challenger, MR2 Spyder).

Where the corrected value differs from TecDoc, the matcher will now report a bodywork conflict for
that model. Those models need a reviewed bodywork context rule, as `volvo_bodywork_reviewed_v1` does
for Volvo SUVs.

## Decisions needed

**1. Gray zone: 125,669 cars.** Both labels are defensible. The rules follow TecDoc, and every
affected rule carries `gray: true` and the alternative:

| Kind | Models (cars) | Rules set (TecDoc) | Alternative |
|---|---|---|---|
| Liftback | Polestar 2 (12,852), Octavia (9,665), Superb (2,044), Toledo, Model S, Panamera, A7, ES90, Rapid | hatchback | sedan |
| Compact / mini MPV | B-Class (11,942), Golf Sportsvan (9,586), Venga (4,950), ix20 (4,374), Golf Plus, Urban Cruiser, Splash, Wagon R, 500L, DS5 | hatchback | multi_purpose_vehicle |
| 4-door coupé | BMW 4 Series GC (9,540), CLA (6,332), Passat CC (3,010), CLS, BMW 2 GC | coupe | sedan |
| Hatchback coupé | Volvo C30 (10,103), Toyota iQ, CLC, C-Class Sportcoupé, Veloster | TecDoc's pick | the other |
| Crossover TecDoc calls hatchback | Kia Stonic (9,574), Kia EV6 (9,524), IONIQ 5 (3,035) | hatchback | suv |
| Other | Peugeot 307 SW, Dacia Jogger, Opel Tigra TwinTop, Kia ProCeed, Megane E-Tech, e-tron GT | TecDoc's pick | the other |

The question for stakeholders: should a vehicle show the class buyers know it by (a Golf Sportsvan is
"a compact MPV", a Stonic "a small SUV"), or the class the parts catalogue files it under? The rules
can be switched by group.

**2. Crossovers that both sources call non-SUV: 34,958 cars, opt-in.** The MINI Countryman (TecDoc:
Estate), SX4 S-Cross (Hatchback), Stonic, EV6 and IONIQ 5 are what most people mean by the
"estate but really an SUV" problem. They are in `optional_crossover_rules`, separate from the
default set.

**3. M1 people carriers.** TecDoc files Multivan, V-Class, Vivaro and Transporter Kombi as "Bus". The
rules set `multi_purpose_vehicle` (9,838 cars).

## Before applying

- **Retire rule `31614f07-dc01-4850-9f69-e67c4be6dc35` first.** Its condition
  "model contains VOLVO / XC40 / XC60" also matched non-SUVs whose model text contains "VOLVO":
  XC70, C70, V70, V60, V50, 940, 240/245, 745, S40, S60, P1800. It set about 180 of them to
  `suv`.
  - Retiring it restores their registry values.
  - It also returns the XC40/XC60 cars to `estate`. The new rules then assert `suv` for them, so
    retire first and apply after.
- Rules cannot filter on `vehicle_scope` (the rule endpoints reject it). With the camper guard, the
  file's rule set matches 0 non-passenger rows. The applied set, with model exclusions instead of
  `body_code2`, touched 24 motorhomes. 7 of those were wrong and have been fixed (see the audit above).
- Applying a resolution rule also updates the linked `core.vehicles` rows in the same transaction.
  A `review` value beats every provider there (see `docs/vehicle-core-design.md`), so the corrections
  reach the Vehicles tab too.

## How this was checked

- **Database:** 283 rules, including every reviewed and opt-in rule, were compiled with the repo's
  `ingestion.vehicle_facts_query.compile_predicate` and counted in the database. All 283 matched
  exactly the number of passenger cars the file states.
- **Per-car evidence:** the automatic rules agree with their own evidence on 99.97% of the cars they
  change (2,156,646 of 2,157,270). Seven rules whose model's own evidence disagreed on more than 5%
  of its cars (1,047 cars) are held back in `held_back_for_review`.
- **Scope:** across the full rule set, 0 rows outside passenger scope are matched.
- **Length:** every reviewed rule was checked against vehicle length (p5 / p50 / p95 of the cars it
  changes):

  | Rule | p5 / p50 / p95 (mm) | Reference |
  |---|---|---|
  | Golf 1K/AU/CD → hatchback | 4,175 / 4,250 / 4,275 | Variant 4,525–4,635 |
  | Yaris AF → hatchback | 3,750 / 3,925 / 3,950 | |
  | Auris (W) → estate | 4,550 / 4,575 / 4,575 | |
  | i30 variant F… → estate | 4,475 / 4,575 / 4,600 | variant B…: 4,225 / 4,275 / 4,375 |
  | BMW 3 GT | 4,800 | |
  | Corolla Cross | 4,450 | |

## What is left

- **430,867 cars without an identifiable model.** Mostly pre-1995 records and registrations that carry
  only brand, kW and year. Resolving their model family first (the brand text often names it, e.g.
  "VW GOLF 1,6") would let these same rules reach them.
- **174,968 cars with an old national code on a model TecDoc lists in several bodies.** Examples:
  Corolla 01 (hatch or sedan), Audi A4/A6 01 (sedan or Avant, same length), Ford Escort, Saab
  99/9000. Once AIS length is imported, a length split resolves hatch vs sedan/estate for most of
  them. Sedan vs estate of the same generation (A4, A6, Passat, 940) usually differ by under 50 mm
  and need the type code or text.
- **Root cause.** The normalizer maps the registry code straight to `bodywork_form` (03 → estate,
  AF → MPV). These rules correct the result. A lasting fix is to derive body type from the identified
  model (family + TecDoc + discriminator) and use the registry code only as a hint.
- **AIS-only vehicles were corrected once.** A vehicle AIS adds later does not get the corrections by
  itself. The same rules have to be carried over again, or the model-based derivation above has to
  exist first.
- **AIS-only vehicles no rule can reach:**
  - 35,822 have no manufacturer in `core.vehicles` (Polestar, Cupra, BYD, XPENG …);
  - 53,428 carry no model text at all (for example 18.7k with only "PEUGEOT").
- **Classic cars (before about 1980) whose registry text names no body keep TecDoc's single label.**
  Examples: Impala, Thunderbird, Skylark. The following were left as applied:
  - a Mercedes SL (R107) registered closed (01) is a coupe;
  - a Fiat 126/127 "Berlina" is a hatchback.

  Review these by hand if classic-car accuracy matters.

The analysis scripts ran from the session scratchpad and are not part of the repo, like the AIS
work. They can become a reproducible CLI with tests if the rules need regenerating after the next
refresh.
