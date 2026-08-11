# TecDoc vehicle ingestion runbook

This runbook is the operational contract for SCRUM-95–98. It deliberately
does not guess offsets in licensed fixed-width files. The provider table
dictionary must be applied during restore and exposed through the stable view
below before NorthStar reads any vehicle data.

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

Automatic promotion requires one active engine, an official supported fuel
code, a resolved displacement, and a valid production start year. Exact Table
155 displacement is preferred; a single Table 120 displacement observed across
the complete restored source is accepted as corroboration.

Manufacturer, ModelFamily, provisional VehicleVariant, Engine and KType Alias
nodes may then be created. Until platform Tables 714/715 are restored, the
accepted graph contract has no path from VehicleVariant to ModelFamily: its
only hierarchy path is VehicleVariant -> Platform -> ModelFamily. The variant
candidate therefore retains `manufacturer_source_key`,
`model_family_source_key`, and `hierarchy_link_status=awaiting_platform_mapping`
in PostgreSQL. Do not create an ad-hoc direct graph relationship to bridge this
gap. Ambiguous engines and unresolved fuel/displacement records stay outside
Neo4j for review.

### Frontend inspection

Open `/normalization-review` and select **TecDoc**. The page chooses the largest
available canonical-promotion batch, avoiding small integration-test batches.
Reviewers can search by KType, manufacturer, model family, engine code or fuel,
then inspect canonical values, the four promotion gates, stable source keys and
the original TecDoc row references. The inspector also explains why the graph
hierarchy remains provisional until Tables 714/715 are available.
