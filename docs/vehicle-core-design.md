# NorthStar vehicle core (`core.vehicles`)

One row per physical, registered vehicle, keyed by an opaque `NOR-<ULID>`. Every
provider (Transportstyrelsen, the AIS VIN export, later ones) and every reviewer or
learned rule *enriches* that one row. Nothing is keyed by a plate, a VIN or a
provider's record number: those are identifiers the vehicle holds for a while, or
links to records that describe it.

Until this existed, a "vehicle" was a row of `vehicle_facts`: one Transportstyrelsen
(TS) record, keyed by its staging row number. That projection stays. It is what
the TS data screen browses and writes rules against (`/v1/ts-records`). The
Vehicles tab reads `core.vehicles` (`/v1/vehicles`).

## Identity

- **`NOR-<ULID>`**: minted once, never reused, never derived from content. The
  prefix is registered in `northstar.node_ids` and the ID contract
  ([graph-schema-design.md](graph-schema-design.md)), but the vehicle is not a graph
  node. Its system of record is PostgreSQL.
- **Look up, then reconcile, then mint.** A writer mints a new ID only after both
  of these fail:
  1. The provider record is already linked (`core.vehicle_source_links`, keyed by
     the provider's own record key). A re-import lands on the same vehicle.
  2. A strong identifier is already held by another vehicle. A full 17-character
     VIN counts. For an old car, the AIS import also accepts a short chassis number
     together with its plate. A short chassis number alone does not identify a car:
     869k old cars carry one, and "000003" belongs to three different Volvos.
- A car seen under two plates (a temporary import plate, then a permanent one)
  is one vehicle with plate history, not two vehicles.
- **A synthetic test record is never a vehicle.** The TS backfill skips any
  record loaded in a fixture batch, that the pipeline quarantined, whose brand
  starts with `TEST/`, or whose plate is `TEST-` followed by digits. No Swedish
  plate, personalized ones included, contains a hyphen. Live carries two such
  test batches (2,000 records, e.g. `TEST-990002171` / `SB100000990002171`) and
  the normalization bundle's fixture (`normalization-bundle-fixture-v1`: one car,
  `TEST001` / `YV100000000000000`). A vehicle minted from one before this rule
  existed is refreshed to `vehicle_scope = 'test_record'`, since vehicles are
  never deleted.

Minting an ID does not make ingestion idempotent. The lookup does, together with
the unique constraints below.

## Tables

| Table | One row per | Invariants the database enforces |
| --- | --- | --- |
| `core.vehicles` | vehicle | ID format; `origin_source` and `registry_status` values; one vehicle per TS record (`ts_record_id` unique); `field_sources`/`field_alternatives` are JSON objects; **no DELETE** (trigger) |
| `core.vehicle_identifiers` | VIN, chassis number or plate, with `valid_from`/`valid_to` | a plate or full VIN is *current* on one vehicle only (partial unique index); a vehicle never holds the same current identifier twice; periods end after they start; **no DELETE** |
| `core.vehicle_source_links` | provider record | primary key `(source_system, source_record_key)`; FK to the vehicle; **no DELETE** |
| `core.vehicle_enrichment_rules` | learned rule | one *active* rule per family and key; support ≥ 1; agreement in [0, 1]; `retired_at` set exactly when retired |

`migrate-vehicle-core` creates all of this idempotently, then verifies that every
named constraint, unique index and trigger exists and is enabled
(`verify_vehicle_core_schema_contract`). It fails the deploy if any of them is
missing or disabled.

A vehicle leaves the register (`registry_status = 'deregistered'`), it is never
deleted. A plate that moves to another car is *closed* on the car it left
(`valid_to`), so that car can still be found by it.

## Which source wins

Every writer turns what it knows into observations and merges them through
`vehicle_core_merge`. The precedence lives in one place, `vehicle_core_fields`,
one policy per column:

0. **`correction`**: a person's correction of this one car. It beats everything,
   a reviewer's rule included: a rule speaks about many cars at once.
1. **`review`**: a reviewer's resolution rule from the TS data screen. It beats
   every provider.
2. **A provider (`transportstyrelsen`, `ais`)**, by the field's policy:
   - `newest`: registry facts that change over a car's life (plate, status,
     vehicle type, colour, fuel after a conversion). The most recent observation
     wins.
   - `ts_first`: technical detail TS describes best (type approval, variant,
     displacement, …). TS wins, and another provider only fills a gap.
   - `any_first`: values TS never had (engine code, weights). Whichever provider
     states the value wins, and the newer one when two do.
3. **`derived`**: computed from the car's own data, for example an EV's power from
   its EV power field.
4. **`rule`**: a learned enrichment rule. It only ever fills a gap.

A value that loses is kept in `field_alternatives`, keyed by its source. When a
review or rule is retired, or a correction is withdrawn, the next-best value
comes back instead of an empty field. `field_sources` records where a winning
value came from, as `<source>[:<ref>][@<YYYY-MM-DD>]`. A field missing from it
came from the record that created the vehicle (`origin_source`,
`origin_observed_on`).

**Dating TS data.** A TS batch is dated by the newest registration it contains,
not by when it was loaded. Otherwise the December 2023 snapshot, loaded in 2026,
would outrank the September 2026 AIS export on every `newest` field.

## Writers

| Command | What it does |
| --- | --- |
| `migrate-vehicle-core` | Schema plus contract check. Runs on every deploy (`infra/production/deploy.sh`). |
| `backfill-vehicle-core` | Walks the per-plate TS survivors in `vehicle_facts`, then links the other TS copies of each car by plate (722k rows from repeated batches). Resumable (`--since`) and idempotent: a re-run changes nothing. Re-running it after a re-normalization is how TS changes reach the vehicles. Disk-guarded. |
| `import-ais-vin-export --file …` | Streams the 16 GB STEP XML once per extract. It matches each record by VIN, or by chassis number plus plate for old cars, then merges it by policy. It marks deregistrations, moves plates, handles A-traktor conversions (a changed vehicle type makes the old EU category stale) and corrected VINs. Changed fuel, gearbox and body codes are re-normalized through the real pipeline. Active passenger cars TS never had are completed with the completion rules, normalized and minted. One run per extract (claimed in `ingest_job_runs`): importing the same file again does nothing. |
| `learn-vehicle-rules [--activate]` | Learns the 13 rule families (below) from `core.vehicles`. Without `--activate` it is a dry run that only prints counts. |
| `apply-vehicle-rules` | Fills gaps from the active enrichment rules. The model families need `--catalog-batch`: their fills are checked by the model guard. |
| `check-model-fills` | Checks every rule-filled model against the car's own model word; `--retract` takes back the contradicted ones. |
| TS data screen rules | Applying or retiring a resolution rule updates the linked vehicles in the same transaction (`vehicle_core_review`), so a rule is never visible on the TS record and missing on the car. |

### Learned rules

A rule says "every vehicle with this key has this value". It is kept only with at
least 5 supporting vehicles and 95 % agreement (both configurable). Rules live in
their own table because the existing rule store copies every rule into every
version, which does not scale to ~368k rules.

- **Enrichment** (learned from AIS, fill TS cars AIS does not cover): engine code
  by make + variant + version (`ENG-VV`), by make + group code (`ENG-GC`), and by
  make + type + displacement + power + fuel (`ENG-TP`); model year (`MY-VB`); max
  weight (`MW-VV`); length (`LEN-VV`).
- **Completion** (learned from TS, keyed by make + group code, complete the cars
  AIS adds): EU category, registry brand/model/type text, variant, displacement,
  4WD flag (`TSC-*`).
- **Model family** (learned from TS, fill the 2.37M registered passenger cars
  whose registry text names only the make or a manufacturer code). A higher bar:
  at least 10 vehicles and 98 % agreement. Tried in this order, most specific
  first:

  | Family | Key | Holdout accuracy |
  | --- | --- | --- |
  | `MOD-VV` | make + variant + version | 99.99 % |
  | `MOD-VIN` | manufacturer + VIN characters 1–8 (manufacturer and descriptor section; full VINs only) | 99.98 % |
  | `MOD-VINL` | manufacturer + VIN characters 1–8 + length (sister models sharing a descriptor: Peugeot 3008/5008, Volvo XC40/C40) | 99.99 % (99.99 % where the VIN alone is ambiguous) |
  | `MOD-VINY` | manufacturer + VIN characters 1–8 + model-year character 10 (a model renamed on one descriptor) | 99.98 % (99.5 % where the VIN alone is ambiguous) |
  | `MOD-TP` | make + type code | 99.97 % |
  | `MOD-VAR` | make + variant | 99.98 % |
  | `MOD-BR` | make + registry brand text | 99.98 % |
  | `MOD-BT` | make + the brand text's model word | 99.99 % |
  | `MOD-MT` | manufacturer + the registry model text, read by `model_text_family` | checked per key |
  | `MOD-BRT` | manufacturer + the whole registry brand text, read the same way (AIS texts that repeat the make, "TOYOTA TOYOTA YARIS CROSS", give no model word) | checked per key |
  | `MOD-PAT` | make + the brand text's model word, read by reviewed patterns (Volvo type codes incl. Amazon/140/164/P 1800/Duett/340/440/460, BMW, Mercedes, Saab model numbers) | checked per key |

  `model_text_family` answers a family TS spells (the most used spelling: "RAV4",
  not "Rav 4") or a reviewed name (`vehicle_model_patterns.REVIEWED_MODEL_NAMES`,
  new models no normalization rule names: EX40, ID.7 Tourer, EV3, Tipo, ...). It
  takes the longest name the text starts with ("ID.7 TOURER GTX" → ID.7 Tourer),
  skips the make and the words the registry writes it by ("VOLKSWAGEN, VW"), reads
  "GR" as Grand, a Volvo "S + V70" after the plus, a Lexus "IS200" as IS, and one
  chassis code in front ("FORD DAW FOCUS") only when the model is all that follows.
  A separator between digits counts: Saab's 93 is not the 9-3. Reviewed aliases
  (`REVIEWED_MODEL_ALIASES`: "TRANS AM" → Firebird, "M3" → 3 Series) never compete
  with a name TS uses itself; reviewed chassis codes (`REVIEWED_CHASSIS_CODES`:
  Honda "RD1" → CR-V, Renault "BA" → Mégane) are kept only where TecDoc gives the
  code to one family. Stellantis "e-" versions read as their family (e-C3 → C3).

- **The guard** refuses a fill the car contradicts:
  - its own text names another family, compared with model numbers respected
    ("ID.4" is not TecDoc's "ID.5 (E39)", "Atto 3" not "ATTO 2"; "GR" may be read
    as Grand);
  - its fuel names another family (`REVIEWED_ELECTRIC_FAMILIES`: an electric
    "FORD MUSTANG" is a Mach-E);
  - a family named like the make ("MINI") with another family's word after it
    ("MINI JCW COUNTRYMAN").

  TecDoc families sold under another name carry it as an alias
  (`match_run_adapters.REVIEWED_EXPORT_NAMES`: BYD "YUAN PLUS" is the Atto 3).
  The matcher looks under a sister maker (`REVIEWED_SISTER_MAKERS`: "FORD USA",
  then "FORD AUSTRALIA") only when the car's model is no family under the make
  itself: TecDoc files every Mustang and Mach-E under FORD USA. The guard keeps
  reading under the make: a word such as "CUSTOM" would otherwise name a 1950s
  Ford on a Transit Custom.
  Siblings TS misread are not counted against a rule
  (`MISREAD_STATED_FAMILIES`: six Mustang GTs TS filed under the Ford GT).

- **Manufacturer** (`MFR-BW`): the brand text's first word, learned from TS
  (10 vehicles, 98 %). AIS sends its own make codes for newer makes ("PO" is
  Pontiac in TS, Polestar in AIS), so ~36k Polestar, Cupra, BYD and XPENG cars had
  none. It runs before the model families, which are keyed on the manufacturer.

  Holdout accuracy: rules learned from 90 % of the vehicles with a known model,
  checked on the other 10 %. The computed keys (`vin_descriptor`, `brand_text`,
  `brand_token`) are SQL expressions in `KEY_EXPRESSIONS`, not columns. The model
  word is the first word after the make ("TOYOTA RAV4" → RAV4), or the word after
  the plus in Volvo's 1990s form ("VOLVO S + V70" → V70). Brand text that is only
  the make ("POLESTAR") gives no key: learned from the 2023 register it would give
  every later model of a one-model make that make's first one.

  `MOD-PAT` covers words no vehicle with a known model shares, mostly old cars. Its
  patterns (`vehicle_model_patterns`) answer only with a model family TS itself
  uses for that make:
  - a word that is such a model ("COROLLA", "307", "9000");
  - BMW series codes (325 → 3 Series);
  - Mercedes-Benz class letters (C 180 → C-Class, ML → M-Class);
  - Volvo type codes (744-883 → 740, 245 → 240, 1421341 → 140).

  A proposed rule is dropped when more than one known vehicle under its key
  disagrees ("C4" is also the C4 Grand Picasso), and a key the statistics already
  learn gets no pattern rule.

  **The model guard.** The holdout only contains vehicles that have a model, so
  it cannot see a model TS never named. Bora, Jetta, Sharan, Sintra and Carens
  share a VIN prefix, type code or variant with a sibling that TS did name, and
  those keys filled the sibling's model: every "VW BORA 1,6" became a Golf.

  So no model fill is made, and `check-model-fills --retract` takes back an
  existing one, when the car's own model word names a catalog model of another
  family. The model word is the registry model field, or the word after the make,
  read by the matcher's own reader (`vehicle_model_guard`). Families are compared
  tolerantly: "3 Series" is "3 (E46)", "Ceed" is "CEE'D", "Mazda3" is "3 (BK)".

  Two things never refuse a fill:
  - a trim word elsewhere in the text ("200 T", "1 6 FSI"), which is weaker
    evidence than the rule;
  - a number, which is an engine size as often as a model ("300 TD").

  **The learned era of a number.** A brand-word rule keyed on a number that is
  not one of the make's model names only fills cars built in the years of the
  vehicles it was learned from, ±2 years (`number_rule_eras`). Mercedes-Benz
  "220" was learned from W220 S-Classes (1999–2006) and would otherwise have made
  every 1970s "220 D" saloon an S-Class. "230" (the R230 SL) and "170" (the R170
  SLK) are the same kind of rule. A number that is a model name (Mazda 6,
  Peugeot 206, Fiat 500) has no era, and neither does a word ("GOLF"). The
  catalog's own model years are no check: it lacks many eras of models TS names
  rightly (a 1969 Pontiac GTO).

  Applying a model family therefore needs `--catalog-batch`.

### A person's KType choice

`ktype` and `match_state` are not merged from sources. They are a copy of the
car's current row in `core.vehicle_ktype_choices` (`manual` with the chosen KType,
`manual_none` for "none of these", empty otherwise), with `field_sources` pointing
at that row as `review:<choice_id>@<date>`. `save_vehicles` leaves the matching
columns of an existing vehicle alone. See `docs/vehicle-ktype-choices.md`.

### A person's correction of one car's data

A person can set or ignore a value the matcher reads for one car. The truth is
`core.vehicle_fact_corrections`, which the matcher is handed directly; the vehicle
carries a copy of every set value under the source `correction:<correction_id>`.
See `docs/vehicle-fact-corrections.md`.

### Ledger

The provenance ledger records changes, clears, plate moves, corrected VINs, new
cars and rule fills. It skips the ~6M plain AIS gap-fills, which stay traceable
through each field's `field_sources` entry. Recording them all would cost about
2–3 GB. Ledger event IDs are derived (UUIDv5) from the import and the vehicle, so a
retried import records nothing twice, and a replay with different content is
rejected.

## Reading it

- `POST /v1/vehicles/search`: conditions on filterable columns (no
  registry-versus-canonical layer: the column already holds the merged value)
  plus free text. A text token matches a current plate or VIN by prefix, the NOR
  ID, **any identifier the car ever held**, or the manufacturer or model family. A
  plate typed with a space ("ABC 123") is also tried as a single identifier.
  Paging is a keyset on the NOR ID, and `matched_rows` is sent on the first page
  only.
  - The text is looked up before the query runs: identifier history by exact
    value, and the manufacturer and model-family names (a skip scan of their
    indexes: about 425 and 1,057 values). The query then carries the vehicles
    and names found as values. Plate and VIN prefixes use `text_pattern_ops`
    indexes, because the database collation (`en_US.utf8`) keeps a default index
    from answering `LIKE 'ABC%'`.
  - On the full register, a plate, VIN, previous plate or NOR ID answers in
    about 0.06 s (36.6 s before), and "volvo v70" in about 2 s.
- `POST /v1/vehicles/facets?field=`: top values, with the field's own clause
  lifted.
- `GET /v1/vehicles/fields`: the column catalog (label, group, filterable).
- `GET /v1/vehicles/{NOR-…}`: every value with its source and the values that
  lost, plus plates and other identifiers over time, source links (the 100 most
  recent, with a total count) and the learned rules behind any value.
- `GET /v1/vehicles/matching/lookup?vehicle_id=`: the TecDoc matcher run on the
  car's **merged** values (an AIS engine code, a reviewer's correction), laid over
  the normalization of the TS record that created it. `overlaid_fields` says which
  inputs came from the vehicle record and from which source. The matching summary
  (`POST /v1/vehicles/matching/summary`) takes the Vehicles filter and evaluates
  vehicles.

The Vehicles tab shows the NOR ID, a registry-status filter and label, where each
value came from (a badge whenever the value did not come from the record that
created the car), the values that lost, and plate history.

## Operating order on a fresh database

1. `migrate-vehicle-core` (deploy does this).
2. `backfill-vehicle-core`: all TS cars. This is the long step, and it can resume.
3. `learn-vehicle-rules --family TSC-… --activate`: the completion rules that new
   AIS cars need.
4. `import-ais-vin-export --file <export.xml>`.
5. `learn-vehicle-rules --activate`, then `apply-vehicle-rules --catalog-batch
   <batch>`: engine code, model family and the other enrichment families. After
   a catalog or rule change, run `check-model-fills --catalog-batch <batch>
   --retract` too.
6. Check the counts against [AIS_TS_UPDATE_OVERVIEW.md](AIS_TS_UPDATE_OVERVIEW.md).

Steps 2–5 need roughly 10 GB of free disk on a full copy.

## First full run (local copy of live, 2026-09-26)

| Step | Time | Result |
| --- | --- | --- |
| `backfill-vehicle-core` | ~80 min | 6,532,408 vehicles from 6,532,590 TS survivors (182 merged by full VIN); 7,255,414 of 7,255,433 TS records linked |
| test-record refresh | seconds | 2,024 vehicles scoped `test_record` (2,000 synthetic `TEST-` plates, 23 already quarantined, and the bundle fixture `TEST001`) |
| `learn-vehicle-rules --family TSC-… --activate` | 39 s | 227,852 completion rules |
| `import-ais-vin-export` | 134 min | 10,814,705 records: 6,485,420 matched (851,867 by chassis number + plate), 660,846 new vehicles, 674,042 deregistered, 11,955 vehicle-type changes, 290 plate changes plus 2,272 plates moved to new cars, 16 corrected VINs |
| `learn-vehicle-rules --activate` | 42 s | 148,260 enrichment rules |
| `apply-vehicle-rules` | 128 s | 95,382 engine codes, 18,055 model years, 18,244 max weights, 13,571 lengths |

The result is 7,193,254 vehicles. Registered passenger cars have engine code
90.8%, model year 99.8%, kW 99.8%, kerb weight 99.1%, max weight 98.5% and
length 99.3%.

**Disk.** About 15 GB: vehicles 6.9 GB, identifiers 5.0 GB, links 1.9 GB and
ledger 1.1 GB. The import also leaves old row versions of every vehicle it
updates, so vacuum the table during and after the import. Locally this needs
`VACUUM (PARALLEL 0)`: Docker's default 64 MB `/dev/shm` is too small for a
parallel vacuum. Live has about 14 GB free and needs more disk before this runs.

**What the run changed in the code.**

- `apply-vehicle-rules` now analyzes the rules table first. Planned against the
  statistics from before learning, the fill became a nested loop that ran ten
  minutes for 31k rows.
- The import no longer re-normalizes every car with a second fuel code (650k):
  the second fuel is compared through the canonical value it produced.

## Known limits

- The Vehicles tab counts ten facets over the whole filter on every load, each
  a full pass over about 7M rows. The first load takes about 7 s locally, and
  cached or approximate counts are the fix. A search narrows the list, not the
  facet counts.
- Matching summary jobs live in the API process's memory (unchanged).
- A vehicle no TS record created (a new AIS car) has no stored TS normalization.
  The matcher sees its merged values alone, without the derivation's candidates.
