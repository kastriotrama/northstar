# TecDoc vehicle ingestion runbook

This runbook is the operational contract for SCRUM-95–98. It deliberately
does not guess offsets in licensed fixed-width files. The provider table
dictionary must be applied during restore and exposed through the stable view
below before NorthStar reads any vehicle data.

## Private portable restore bundle

Approved team members can download the versioned PostgreSQL and Neo4j restore
bundle from [Google Drive](https://drive.google.com/file/d/1H-2XmZucsF2SsZxcKLMqQN-FYd36xLyg/view?usp=sharing).

The bundle is aligned with commit `c039eea` / PR 29 and contains derived TecDoc
data, restore instructions, a manifest, and SHA-256 checksums. Treat it as
licensed private data: do not commit it to Git or redistribute it outside the
approved team. Verify `SHA256SUMS` before restoring and follow the included
`README-restore.md`.

## Source currently available

- Delivery: `REFERENCE_DATA_0326`
- Release: `0326`
- Format marker in `001.dat`: `2.70`
- License reference: optional operational metadata. When it is unavailable,
  NorthStar records `not_provided`; provider source files still stay out of Git.

## Authoritative vehicle hierarchy interpretation

Table `120` contains KType passenger-car objects with their basic vehicle
information. Its primary key is `KTypNo` (`KTypNr` in the legacy/German field
names). A Table 120 row is the vehicle object itself, not another navigation
folder.

The hierarchy is interpreted as follows:

1. **Level 1 - Manufacturer:** Table `100`, keyed by `ManNo`/`HerNr`.
2. **Manufacturer group:** under each manufacturer, create the applicable
   group branches indicated by the Table 100 flags: `PC`, `CV`, `Axle`,
   `Engine`, `Transmission`, and `LCV`.
3. **Level 2 - Model series:** Table `110`, keyed by `KModNo`/`KModNr` and
   linked to Table 100 by `ManNo`/`HerNr`. Model-series folders appear only
   beneath the `PC`, `CV`, or `LCV` manufacturer groups.
4. **Level 3 - Passenger-car object:** Table `120`, keyed by
   `KTypNo`/`KTypNr` and linked to its Table 110 model series by
   `KModNo`/`KModNr`. These are actual vehicle/KType objects, not folders.

Engine information extends each Table 120 vehicle object through these joins:

- Table `125` links a Table 120 `KTypNo`/`KTypNr` to an engine
  `EngNo`/`MotNr`. Its vehicle-engine identity is the combination of KType and
  engine allocation keys; the source sequence and applicability fields must
  also be preserved when multiple allocations exist.
- Table `155` contains the engine object keyed by `EngNo`/`MotNr`, obtained
  through Table 125.

In compact form:

```text
Table 100 Manufacturer
└── PC / CV / Axle / Engine / Transmission / LCV group
    └── Table 110 Model series (PC, CV, or LCV only)
        └── Table 120 KType passenger-car object
            └── Table 125 engine allocation -> Table 155 Engine
```

NorthStar's canonical graph does not persist the display-only manufacturer
group folders as vehicle entities. It preserves the Table 100 capability
flags as source evidence, while Manufacturer, ModelFamily, VehicleVariant,
Engine, and KType Alias remain the canonical data objects.

## 1. Restore and validate

1. Restore the provider dump/files into a dedicated PostgreSQL schema named
   `tecdoc_source`. Never restore into `public`, `core`, or `staging`.
2. Apply the provider's matching release `0326` table dictionary.
3. Create `tecdoc_source.northstar_vehicle_tree`. It must expose exactly the
   columns listed in `ingestion.tecdoc.extraction.VEHICLE_TREE_COLUMNS`, one
   row per KType, ordered/stably keyed by `ktype_id`.
4. Include the original provider table and row identifiers in
   `source_row_refs`. Shared engines, transmissions, and bodywork may appear
   on many KTypes; they must retain the same provider ID on every row.
5. Compare counts: provider passenger KTypes = view rows = distinct
   `ktype_id`. A duplicate KType or missing required key stops ingestion.

The adapter view owns provider-specific joins. It joins manufacturer, model,
KType/vehicle variant, engine, transmission, and bodywork tables and their
language-description tables. Optional facts are `NULL`; fabricated platform,
engine, transmission, or bodywork values are forbidden.

## 2. Configure and run

```bash
export TECDOC_SOURCE_PATH=/licensed/source/REFERENCE_DATA_0326
export TECDOC_SOURCE_VERSION=0326
export TECDOC_FORMAT_VERSION=2.70
# Optional: export TECDOC_LICENSE_REFERENCE=<internal-license-reference>
export TECDOC_SOURCE_CHECKSUM=<sha256-of-source-manifest>
export TECDOC_SOURCE_SCHEMA=tecdoc_source
northstar-ingest tecdoc --batch-id tecdoc-0326-initial
```

The job records immutable batch metadata, stable source keys, canonical
candidates and opaque NorthStar IDs. Re-running the identical batch is safe:
the candidate and ledger writes are idempotent. Reusing a batch ID with a
different version, checksum, path, recorded license reference, or count is rejected.

## 3. Reconciliation and evidence

- `core.tecdoc_source_batches` records source/version/checksum/count/status.
- `core.tecdoc_identity_registry` reuses the same opaque ID for a stable
  TecDoc entity key across later releases.
- `core.tecdoc_canonical_candidates` holds mapped candidates before graph
  loading. Multiple KTypes share one engine/transmission/bodywork candidate.
- `core.enrichment_ledger` records source version, batch, source key and raw
  row references for every candidate. It is append-only at database level.

Sample tracing starts with a KType alias candidate, follows its
`target_source_key` to the vehicle variant, and uses each candidate's
`source_row_refs` to locate the exact restored provider rows.

### Engine relationship promotion gate

Table 125 evidence first lands in `core.tecdoc_candidate_relationships` as one
candidate per distinct `(KType, Engine)`, with all country/date applicability
rows nested as evidence. A candidate is promoted to Neo4j `USES_ENGINE` only
when that canonical VehicleVariant has exactly one resolved engine. If a KType
has several engines, resolution must either select one with supporting market
evidence or split it into separate one-engine VehicleVariants. The graph writer
rejects a second engine on an existing variant rather than violating the
accepted singular relationship contract.

### Canonical KType promotion

Automatic promotion requires one active engine, official mapped fuel evidence,
a resolved displacement, and a valid production start year. A single official
fuel is stored in the scalar `fuel_type`. An official mixed fuel is stored with
`fuel_type=null`, the exact TecDoc code and label, `fuel_representation=mixed`,
and the complete immutable `fuel_components` set. A TS fuel that intersects
that component set is compatible but non-confirming: it removes an unsupported
fuel conflict but adds no positive score. Disjoint fuel evidence remains a hard
conflict. Unknown or unmapped fuel still fails closed. Exact Table 155
displacement is preferred; a single Table 120 displacement observed across the
complete restored source is accepted as corroboration.

Three gate refinements (2026-10-02; they take effect only in a newly built batch):

- **Electric motors.** Displacement does not apply to a KType with engine type
  `040` (KT 080, battery electric), one active engine labelled Electric, a Table
  120 fuel of electric (or none), and no displacement in Table 120 or Table 155.
  It promotes with `displacement_cc=null` and
  `displacement_source=not_applicable_electric`. The rule keys on the engine
  type, not the fuel label. Hydrogen fuel-cell KTypes (engine type 040, Table 120
  fuel hydrogen; 9 in the 0326 delivery) are left out on purpose and stay
  `displacement_unresolved`: including them is a separate decision.
- **Table 155 without an upper value.** The 0326 delivery has no
  `displacement_cc_to` on any link, so `table_155_exact` never applies. After the
  exact value and the Table 120 consensus, a lone `displacement_cc_from` is
  accepted as `table_155_from_only` when the KType's own Table 120 displacement
  is equal to it or absent. A KType whose own displacement differs (a shared
  engine listed at 1,984 cc on a 1,968 cc KType) stays `displacement_unresolved`,
  so no promoted KType's displacement differs from its Table 120 value.
- **Petrol/Gas engines.** The KT 088 labels `Petrol/Gas` and `Petrol/Alcohol/Gas`
  stay unmapped engine fuel evidence (`fuel_representation=unmapped`, no
  components, engine candidate `fuel_type=null`). The gate alone accepts such an
  engine when the KType's Table 120 fuel is one mapped fuel the label contains
  (petrol, lpg, cng; also ethanol for `Petrol/Alcohol/Gas`), and the variant is
  promoted with that vehicle fuel. Do not add these labels to
  `_MIXED_ENGINE_FUEL_LABELS`: that changes the matcher's fuel components for
  every KType sharing the engine and was measured to turn resolved cars into ties.

Manufacturer, ModelFamily, provisional VehicleVariant, Engine and KType Alias
nodes may then be created. Every promoted variant receives a `VARIANT_OF`
relationship to its known ModelFamily, which connects to Manufacturer through
`MADE_BY`. Platform is optional: create `BUILT_ON` only when Tables 714/715 or
another approved source supplies reliable platform evidence. The variant
candidate retains both hierarchy source keys and records
`hierarchy_link_status=model_family_linked_platform_optional`. Ambiguous
engines and unresolved fuel/displacement records stay outside Neo4j for review.
The matcher may inspect every preserved KType-to-engine candidate and compare a
TS engine code against the full engine-code set, but must not select one engine
or mark the KType graph-safe from that overlap alone.

Bodywork is optional but directly available as the Table 120 KT 086 code. Link
it with `HAS_BODY`, preserve the code, and use Tables 020/030/052 for its
official English display label with
`terminology_status=canonical_mapped_from_official_english`.
Create the canonical relationship only for codes in the reviewed KT 086 to
NorthStar mapping. Unmapped official forms retain their code and label with
`bodywork_link_status=review_required`; do not force them into a nearby form.
Transmission is also optional: resolve Table 547 KType allocations through Table 544 and create
`USES_TRANSMISSION` only for one distinct transmission. Preserve multiple
allocations as `transmission_link_status=ambiguous` without choosing one.

Production months: promotion keeps TecDoc's `YYYYMM` start and end beside the
years as `month_from` / `month_to`. The matcher compares a car's build month with
them: a month outside a KType's run inside a covered year is unverified (a small
penalty, never a conflict), since TecDoc's month boundaries are approximate.

The month rule is deliberately narrow (`FuzzyMatchConfig.production_month_tolerance`
= 0 months, measured 2026-10-01 on the 30k and an independent 20k):

- Months choose only within the car's model line: the TecDoc name without chassis
  code, generation and the car's own body word ("LEGACY IV Estate" and "LEGACY V
  Estate" are one line). A KType of the car's own line is penalized only when a
  KType of that line without a conflict covers the build month; a KType of another
  line ("PAJERO SPORT", "IBIZA IV SC", "PASSAT ALLTRACK") whenever its run misses
  the month. A car is never sent to another line because that line's run covers
  its month.
- A body word naming another body than the car's keeps its KType another line: for
  a registered SUV, "GLC Coupe" is not the line of "GLC". Makers' own estate names
  count as body words (T-Model, Turnier, Grandtour, Sportstourer, ST, Shooting
  Brake, Station Wagon, Weekend, Aerodeck, Variable, Traveller); names that tell
  a door count or another car apart (Sportback, SC, GTC, Allroad, Cross Country)
  do not, so months never choose between them.
- A KType that conflicts with the car gets no protection from its line.
- Result against no months (prod-v4 vs prod-v2): 30k 64.2% -> 65.5% resolved
  (+410, -32 to review, 8 moved), 20k 64.1% -> 65.4% (+279, -24, 6 moved).
- Widening the tolerance to 1-3 months loses gains (686 -> 563 -> 444 -> 341); any
  change to it or to the line rule needs both samples re-measured car by car.

#### Composing a batch when a delivery lacks a table (prod-v4, 2026-10-01, local)

The `0326` delivery on the Mac mini has no Tables 547-549, so a rebuild from it
(`prod-v3-20260930`) carries months but no transmissions, while `prod-v2-20260914`
(built from a source with Table 547) carries transmissions but no months. Every
KType is otherwise identical in the two. `prod-v4-20261001` takes every row of v2
(candidates with their node IDs, the 829 transmission entities, all relationships)
and adds only `month_from` / `month_to` from v3, KType by KType, in one
transaction under a new batch row; v2 and v3 stay untouched. Verified: equal row
counts per entity type, 0 rows differing from v2 beyond the two month keys, 0 month
values differing from v3, 0 relationships differing from v2. A full rebuild from a
delivery that includes Tables 547-549 replaces this.

Drive is a VehicleVariant property sourced from Table 120 KT 082. Map Front-
Wheel Drive to `fwd`, Rear-Wheel Drive to `rwd`, and all explicit selectable,
permanent, or electronically regulated all-wheel forms to `awd`. Preserve
mechanism values such as Chain, Direct, Cardan, Belts, Vario and Direct 2x2 as
review evidence without inferring wheel drive.

#### Electrification and reading guards in the matcher (2026-10-01)

Both catalog loaders (`load_postgres_ktype_catalog`, and the graph's
`load_ktype_catalog`) read the variant's `tecdoc_engine_type_code` (KT 080) into
`VehicleCandidate.electrification`: 046 plug-in hybrid, 047 range extender, 048
full hybrid, 049 mild hybrid, 040 battery electric, 001-004 combustion. A missing
or other code is unknown, and no check ever reads unknown as "not a plug-in". The
car's side is normalization's `electrification_type` (ELHYBRID or a word such as
eTSI: `hybrid`; LADDHYBRID: `plug_in_hybrid`).

These checks only send a car to review, or change what a hard-conflict car is
shown: none raises a route or resolves a car to another KType. Each adds a reason
code and a routing-gate entry to the decision trace, so the Matching tab's verdict
names it:

| Reason | When |
|---|---|
| `match_guard:plug_in_power_unverified` | Electric plus combustion fuel, not registered `hybrid`; only exact power set a KType TecDoc knows is no plug-in apart from a plug-in or range-extender sibling (the registry gives engine power, TecDoc a plug-in's system power). The two tie. |
| `electrification_conflict` | Registered plug-in, KType known to be none; or registered `hybrid`, KType a plug-in in a TecDoc family that has a full or mild hybrid KType. |
| `hard_conflict_replaced:<field>` | The suggestion had a hard conflict; the best reading that contradicts nothing, inside the registry family (or the conflict's own family), is shown instead. The car stays in review. |
| `reading_disagreement:outside_registry_family` | The KType came from an alternative model value, outside the registry family, while another reading lies inside it (IONIQ5 read as IONIQ 6). |
| `reading_disagreement:other_matcher_family` | On the winning model value the other matcher (base or reviewed aliases) also reaches provisional or resolved, in another family. |
| `reading_disagreement:other_value_ktype` | Another model value, as text, also resolves, to another KType ("V60" against "V60 CROSS COUNTRY"). |

The evaluation key gains `("electrification", ...)` and `("registry_family", ...)`
when a car has them, so those cars' chunk signatures change once.

Measured with the real implementation on prod-v4 (every car of both samples
against the design's baseline): 20k 102 resolved and 13 provisional cars to
review, 13 hard conflicts relabelled to review; 30k 161, 19 and 35. Every other
car is unchanged: 0 resolved cars moved to another KType, 0 cars newly resolved.
Known costs: a 2024 V60 Cross Country B5 that its own text got right; a car the
registry wrongly calls a plug-in (a Lexus CT200h); and likely LADDHYBRID Outlander
IVs, since TecDoc codes that plug-in "2.4 Hybrid" 048. Kia cars a rule
filled "Sorento" whose "SL" text reads the Sportage III keep their Sorento
suggestion, since a replacement stays in the registry family. Settling the plug-in
ties by kerb weight is open stakeholder question S15.

### Frontend inspection

Open `/normalization-review` and select **TecDoc**. The page chooses the largest
available canonical-promotion batch, avoiding small integration-test batches.
Reviewers can search by KType, manufacturer, model family, engine code or fuel,
then inspect canonical values, Bodywork and Transmission allocation, the four
promotion gates, stable source keys and original TecDoc row references. The
inspector also explains that Platform is optional until reliable evidence is
available.

The TecDoc workspace also provides separate Manufacturer, Model family, Engine
and Fuel browsers. Each list reports the number of promoted KTypes using the
canonical value and shows up to 12 example KTypes, making shared entities and
fuel coverage reviewable without opening vehicles one at a time.

Run the complete safe promotion with:

```bash
northstar-ingest promote-tecdoc-canonical \
  --batch-id tecdoc-0326-canonical-full \
  --source-path /licensed/source/REFERENCE_DATA_0326 \
  --reference-path /licensed/reference/REFERENCE_DATA_0326 \
  --source-version 0326 --format-version 2.70 \
  --source-checksum <source-manifest-sha256> --chunk-size 500
```

The command parses the complete source before writing, uses official English
fuel labels, persists only safe candidates, loads Neo4j in bounded chunks, and
reconciles the final PostgreSQL candidate count. Reusing the identical batch
is safe and produces zero additional candidates.

To rebuild and validate a complete PostgreSQL matcher catalog without changing
Neo4j, add `--candidate-catalog-only`. This mode retains both graph-safe KTypes
and explicitly labelled `candidate_only` KTypes, reconciles the immutable batch,
and reports zero graph rows and zero graph chunks. Use a new batch ID whenever
code or evidence semantics change; never retrofit a completed batch.

KTypes without a Table 125 row are not engine-less vehicles. Table 120 already
contains their vehicle-level engine facts. Promote them as provisional variants
with `engine_link_status=allocation_missing`; preserve power, technical
displacement, KT 182 fuel and KT 080 engine type, and omit `USES_ENGINE` until a
real Table 155 allocation is available. Electric engine type `040` does not
require displacement under the official format contract.
