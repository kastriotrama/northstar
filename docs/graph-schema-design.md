# Graph Schema Design — Nodes & Relationships

| Field | Value |
|---|---|
| Version | `0.3` |
| Status | Accepted node, relationship, and ID contract |
| Owner | NorthStar backend team |
| Jira story | SCRUM-12 (nodes), SCRUM-13 (relationships), SCRUM-14 (IDs) |
| Scope | Node labels, properties, relationships, cardinality, IDs, query patterns, examples, and invariants |
| Last reviewed | 2026-07-15 |

Canonical graph model for the Neo4j knowledge graph (Phase 1, Stories 2.1,
2.2, and 2.3). This document is canonical but incomplete until later Epic 2
stories extend it.

Relationship names, direction, cardinality, and edge properties are defined
in §5 (SCRUM-13), and ID generation is defined in §7 (SCRUM-14).
Constraints/indexes and merge/split mechanics remain owned by their stories
per the table below.

## Decision ownership

| Decision | Owner |
|---|---|
| Labels, properties, types, and nullability | SCRUM-12 |
| Alias node semantics and invariants | SCRUM-12 |
| Relationship names, direction, and cardinality | SCRUM-13 |
| ID minting implementation | SCRUM-14 |
| Constraints, indexes, and migrations | SCRUM-15 |
| Merge/split mechanics and `:Superseded` lifecycle | SCRUM-68 |

## 1. Core principles

1. **Opaque internal IDs.** Every node is keyed by an internal ID that carries
   no source meaning: `<PREFIX>-<ULID>`, e.g.
   `ENG-01ARZ3NDEKTSV4RRFFQ69G5FAV` (prefix + 26-character ULID). External
   codes (TecDoc k-type, engine codes, plates, VINs) are NEVER node IDs — they
   enter the graph only as `Alias` nodes. IDs are never reused, including
   after node merges or splits. Prefixes are accepted by SCRUM-12; the ULID
   payload and generation utility are defined in §7 by SCRUM-14. Shortened IDs
   in diagrams and examples (e.g. `ENG-04D`) are illustrative only and
   invalid for real writes.
2. **Intrinsic vs. pairing facts.** A property lives on a node only if it is
   true of the thing itself everywhere it appears. Anything specific to a
   combination (the 231 hp vs 258 hp tune of the same OM642 engine) lives on
   the relationship, not the node. This is what lets 40 vehicle variants share
   one Engine node.
3. **One source of truth per fact.** The alias-to-node mapping is the
   `REFERS_TO` edge and nothing else — `Alias` has no `target_node_id`
   property to drift out of sync during node merges.
4. **Confidence-gated membership.** Nodes created from records scoring
   0.65–0.90 carry the secondary label `:Provisional` (see §4).

## 2. Type system and conventions

Property types used in the tables below:

| Type | Neo4j storage | Convention |
|---|---|---|
| `string` | String | UTF-8, NFKC-normalized, trimmed; canonical_name fields are dictionary-canonicalized (Epic 4 Stage 1b) |
| `int` | Integer | — |
| `float` | Float | Confidence values are 0.0–1.0 |
| `bool` | Boolean | Never null — absence of the fact means `false` |
| `year` | Integer | 4-digit Gregorian year; ranges use `year_from`/`year_to`, `year_to = null` means "still current" |
| `string[]` | List of String | Order not significant; no duplicates |
| `enum(...)` | String | Only the named values are valid; enforced by the normalization pipeline (graph does not enforce enums) |
| `datetime` | DateTime | UTC, set by the graph writer |

**Required vs nullable:** `required` means the graph writer must not create
the node without it; `nullable` means the value may be unknown at creation
and can be enriched later. Required properties are never null.

**Common metadata (every node, all labels):**

| Property | Type | Required | Meaning |
|---|---|---|---|
| `id` | string | required, unique per label | `<PREFIX>-<ULID>`, minted by the central ID utility (SCRUM-14) |
| `created_at` | datetime | required | First write |
| `updated_at` | datetime | required | Last write |

## 3. Node labels

Eight labels. Prefixes are fixed and appear in every example.

### 3.1 Manufacturer (`MFR-`)

The vehicle brand as marketed (Mercedes-Benz), not the corporate group
(Daimler AG). Group structures are out of scope for Phase 1.

| Property | Type | Required | Example | Notes |
|---|---|---|---|---|
| `canonical_name` | string | required | `"Mercedes-Benz"` | Dictionary-canonical form; `"MERCEDES BENZ"`, `"mercedes-benz"` are aliases |
| `country` | string | nullable | `"DE"` | ISO 3166-1 alpha-2 of brand origin |

### 3.2 ModelFamily (`FAM-`)

The name people actually say — "E-Class", "XC90", "Golf". Entry point for
structured-form search. Not a generation: the E-Class family spans W211,
W212, W213 platforms.

| Property | Type | Required | Example | Notes |
|---|---|---|---|---|
| `canonical_name` | string | required | `"E-Class"` | Unique within a manufacturer, not globally ("Golf" exists once, under VW) |
| `segment` | enum(`city`, `compact`, `midsize`, `executive`, `luxury`, `suv`, `van`, `pickup`, `sports`) | nullable | `"executive"` | Marketing segment; enum values maintained in the Epic 4 dictionaries |

### 3.3 Platform (`PLT-`)

A generation/chassis of a model family: W212 is "the 2009–2016 E-Class".

| Property | Type | Required | Example | Notes |
|---|---|---|---|---|
| `platform_code` | string | required | `"W212"` | Manufacturer chassis code; unique within manufacturer |
| `generation` | string | nullable | `"4"` | Ordinal within the family, when known |
| `year_from` | year | required | `2009` | Start of production |
| `year_to` | year | nullable | `2016` | `null` = still in production |
| `facelift` | bool | required | `false` | `true` for mid-cycle refresh sub-generations (e.g. W212 MOPF) modeled as their own Platform |

### 3.4 Engine (`ENG-`)

An engine design, shared across every vehicle that uses it. **Intrinsic
facts only** — power, torque, and emission standard vary per installation and
live on the `USES_ENGINE` edge (SCRUM-13).

| Property | Type | Required | Example | Notes |
|---|---|---|---|---|
| `engine_code` | string | required | `"OM642"` | Manufacturer engine family code |
| `displacement_cc` | int | required | `2987` | Exact cc, not the marketing "3.0" |
| `fuel_type` | enum(`petrol`, `diesel`, `electric`, `hybrid_petrol`, `hybrid_diesel`, `lpg`, `cng`, `hydrogen`) | required | `"diesel"` | |
| `configuration` | string | nullable | `"V6"` | Cylinder layout when known |

### 3.5 Transmission (`TRN-`)

A transmission design; same shared-component amortization value as Engine —
the 722.9 ships in hundreds of variants.

| Property | Type | Required | Example | Notes |
|---|---|---|---|---|
| `transmission_code` | string | required | `"722.9"` | Manufacturer code |
| `canonical_name` | string | nullable | `"7G-TRONIC"` | Marketing name when distinct from the code |
| `type` | enum(`manual`, `automatic`, `dct`, `cvt`, `amt`) | required | `"automatic"` | |
| `gears` | int | nullable | `7` | |

### 3.6 BodyType (`BDY-`)

Body style vocabulary node. Small, closed-ish set shared by all variants.

| Property | Type | Required | Example | Notes |
|---|---|---|---|---|
| `canonical_name` | string | required | `"sedan"` | From the body-type synonym dictionary (Epic 4): sedan, wagon, coupe, convertible, hatchback, suv, van, pickup, chassis_cab |
| `door_count` | int | nullable | `4` | Distinguishes 3- vs 5-door hatchback |

### 3.7 VehicleVariant (`VEH-`)

**The resolution target.** A specific sellable configuration: platform +
engine + transmission + body + trim + years + markets. One plate/VIN/k-type
resolves to exactly one live VehicleVariant.

| Property | Type | Required | Example | Notes |
|---|---|---|---|---|
| `market` | string[] | required, may be empty | `["SE", "DE"]` | ISO 3166-1 alpha-2 markets where sold. Starts as `[]` on TecDoc-derived nodes (TecDoc does not carry market data) and grows as registration sources confirm; never null |
| `trim_level` | string | nullable | `"Avantgarde"` | Trim/equipment line when known |
| `drive_type` | enum(`fwd`, `rwd`, `awd`) | nullable | `"rwd"` | |
| `year_from` | year | required | `2009` | Variant production start |
| `year_to` | year | nullable | `2013` | `null` = still produced |

Identity note: a VehicleVariant has no `canonical_name` of its own — its
display name is assembled by traversal (Manufacturer + ModelFamily + engine
badge + body). Storing an assembled name would denormalize facts owned by
neighboring nodes.

### 3.8 Alias (`ALI-`)

The bridge between external vocabularies and internal nodes. Every external
code — k-type, TecDoc engine code, registration plate, VIN, clerk-typed model
string — is an Alias pointing at exactly one live node via `REFERS_TO`.

| Property | Type | Required | Example | Notes |
|---|---|---|---|---|
| `alias_text` | string | required | `"ABC123"` | Normalized form (Epic 4 Stage 1a) of the external string; **non-unique lookup value**, mutable when normalization rules improve |
| `alias_type` | enum(`k_type`, `engine_code`, `body_code`, `plate`, `vin`, `model_name`) | required | `"plate"` | What kind of identifier this is; independent of where it came from |
| `source_system` | enum(`tecdoc`, `transportstyrelsen`, `manual`) | required | `"transportstyrelsen"` | Which source asserted this mapping; new sources extend the enum |
| `source_record_key` | string | required | `"vehicle-abc123"` | Stable provider record containing the assertion; for manual assertions, use a stable change-request or batch key |
| `source_assertion_key` | string | required | `"vehicle-abc123:plate:0"` | Stable source-local identity for this individual alias assertion |
| `confidence` | float | required | `0.97` | 0.0–1.0 confidence of the mapping, from the Epic 4 scoring gate; mutable |

For manual assertions, keep record and assertion identity separate. Use a
stable key such as `manual:<change-request-or-batch-id>` for
`source_record_key` and a distinct `manual:<individual-assertion-id>` for
`source_assertion_key`.

`alias_type` and `source_system` are deliberately separate dimensions: a
plate is asserted by Transportstyrelsen, a k-type by TecDoc, and the same
model-name string may be asserted by both. Conflating them would make "all
aliases from source X" and "all plate aliases" unanswerable.

**Identity.** Logical uniqueness is over:

```text
(source_system, source_assertion_key)
```

Identity fields (`source_system`, `source_record_key`,
`source_assertion_key`, `alias_type`) are immutable once written; lookup and
scoring fields (`alias_text`, `confidence`) are mutable. This keeps assertion
identity stable when normalization rules improve or a provider corrects a
value — the existing Alias is updated instead of a duplicate being minted.

When the provider supplies a stable identifier for an individual assertion,
use it directly as `source_assertion_key`. Otherwise derive it
deterministically from stable source-local components:

```text
<source-record-key>:<field-name>:<value-position>
```

Positional derivation (`<value-position>`) is only valid when the source
guarantees stable value order within a record across dumps. If order is not
guaranteed, use the provider's stable per-value identifier. If none exists,
the ingestion service must persist a generated assertion key in its
provenance mapping and reuse it only when the source can correlate an update
to the same assertion. If that correlation is impossible, create a new
assertion and explicitly retract or supersede the old assertion. Do not derive
stable identity from a hash of mutable value content. Document the chosen
strategy per source in the ingestion service.

Examples:

```text
tecdoc:vehicle-82931:k_type:0
transportstyrelsen:vehicle-abc123:plate:0
manual:assertion-01ARZ3NDEKTSV4RRFFQ69G5FAV
```

**Rules (deliberate, load-bearing):**

- **No `target_node_id` property.** The `REFERS_TO` edge is the single source
  of the alias-to-node mapping. A property copy would diverge from the edge
  the first time a node merge re-points edges (SCRUM-68) and silently
  corrupt resolution. Every Alias has exactly one outgoing `REFERS_TO` edge
  to a live node.
- **`alias_text` is indexed but never unique.** Two source assertions may
  expose identical text without being forced into the same Alias node or the
  same canonical target — engine codes and model names are not guaranteed
  unique within a provider.
- **k-type is always an Alias**, never a label or a property on
  VehicleVariant. One variant may map to multiple k-types (TecDoc splits
  finer than we do in places). Dual-alias pattern: k-type aliases point at
  VehicleVariant nodes; TecDoc engine/body codes point at Engine/BodyType
  nodes directly.
- Aliases are per-source: the same text `"E350"` from TecDoc and from a
  clerk-typed Transportstyrelsen field are two Alias nodes with different
  `source_system` and confidence.
- The exact Neo4j constraint and lookup indexes implementing this identity
  are owned by SCRUM-15.

## 4. `:Provisional` secondary label

Nodes created from records scoring 0.65–0.90 in the normalization gate carry
`:Provisional` in addition to their primary label (e.g.
`:VehicleVariant:Provisional`).

- Excluded from customer-facing resolves by default (resolve queries filter
  `NOT n:Provisional`).
- Promoted by removing the label when a second independent source confirms
  the node; demotion/merge handled by the SCRUM-68 merge procedure.
- `:Provisional` carries no properties of its own; the confidence that put it
  there lives on the Alias/edge that created it and in the enrichment ledger.

Nodes retired by a merge receive `:Superseded` plus a `SUPERSEDED_BY` edge
(SCRUM-68); they are never deleted, so ledger rows stay resolvable.

## 5. Relationships

### 5.1 Direction and ownership rule

Store every edge in the direction shown in §5.2. Queries may traverse either
way, but writers must not create inverse duplicates for read convenience.
The hierarchy has one source of truth: a variant reaches its family only via
`BUILT_ON` and `PLATFORM_OF`; there is no direct variant-to-family edge that
could disagree with its platform.

Canonical domain relationships are singular facts, not per-source assertion
records. A writer resolves evidence before updating the edge and retains the
source assertions in the ingestion/enrichment ledger. It must not create
parallel domain edges for conflicting evidence because repeated ingestion
would otherwise be unable to update those assertions idempotently.

### 5.2 Relationship catalog

| Relationship | Start label | End label | Intent | Phase 1 properties |
|---|---|---|---|---|
| `MADE_BY` | `ModelFamily` | `Manufacturer` | Assign one marketed family to its manufacturer | None |
| `PLATFORM_OF` | `Platform` | `ModelFamily` | Assign one chassis/generation to its model family | None |
| `BUILT_ON` | `VehicleVariant` | `Platform` | Select the platform for a sellable variant | None |
| `USES_ENGINE` | `VehicleVariant` | `Engine` | Select the resolved engine installation | `power_kw`, `torque_nm`, `emission_standard` |
| `USES_TRANSMISSION` | `VehicleVariant` | `Transmission` | Select the resolved transmission design | None |
| `HAS_BODY` | `VehicleVariant` | `BodyType` | Select the variant's body configuration | None |
| `REFERS_TO` | `Alias` | Any non-`Alias` canonical node | Map one source assertion to one live canonical target | None; identity, provenance, and confidence remain on `Alias` |

Only `USES_ENGINE` carries Phase 1 pairing facts:

| Property | Type | Required | Meaning |
|---|---|---|---|
| `power_kw` | int | nullable | Rated output for this vehicle installation, in kilowatts |
| `torque_nm` | int | nullable | Rated peak torque for this installation, in newton-metres |
| `emission_standard` | string | nullable | Homologation standard for this installation, such as `"Euro 5"` |

`SUPERSEDED_BY` is intentionally excluded from this catalog. SCRUM-68 owns
its target rules, lifecycle, and migration behavior.

### 5.3 Cardinality and write rules

Incoming cardinality is `0..*` for all seven relationships. Outgoing rules
are:

| Relationship | Outgoing cardinality | Provisional exception |
|---|---|---|
| `MADE_BY` | `ModelFamily` `1..1` | None |
| `PLATFORM_OF` | `Platform` `1..1` | None |
| `BUILT_ON` | `VehicleVariant` `1..1` | A provisional variant may temporarily have `0..1` |
| `USES_ENGINE` | `VehicleVariant` `1..1` | A provisional variant may temporarily have `0..1` |
| `USES_TRANSMISSION` | `VehicleVariant` `1..1` | A provisional variant may temporarily have `0..1` |
| `HAS_BODY` | `VehicleVariant` `1..1` | A provisional variant may temporarily have `0..1` |
| `REFERS_TO` | `Alias` `1..1` | None; do not create a zero-target Alias |

`:Provisional` relaxes only the listed minimum; it never relaxes a maximum.
Every graph writer validates the current outgoing set in the same transaction
as its write and uses `MERGE` on the complete start/relationship/end pattern.
Parallel duplicates are invalid even when they point to the same endpoint.
New `REFERS_TO` edges may target provisional nodes, but never `:Superseded`
nodes; customer-facing resolves continue to filter provisional targets.

Invalid writes include:

```text
(Engine)-[:USES_ENGINE]->(VehicleVariant)              // inverted
(variant)-[:BUILT_ON]->(w212), (variant)-[:BUILT_ON]->(w213)
(alias)-[:REFERS_TO]->(engine), (alias)-[:REFERS_TO]->(variant)
(alias)-[:REFERS_TO]->(:VehicleVariant:Superseded)
```

### 5.4 Naming rules

- Names are stable uppercase `SNAKE_CASE` verb phrases.
- The name reads naturally from start to end: *variant USES engine*,
  *platform PLATFORM OF family*, *alias REFERS TO target*.
- `PLATFORM_OF` is an accepted exception to the verb-phrase rule (it reads
  as a noun). It is kept deliberately — do not rename it for symmetry; the
  stability rule outweighs the aesthetic one.
- Do not add generic `HAS`, `LINKED_TO`, `RELATED_TO`, or inverse duplicates.
- A new name must be added to §5.2 in the same PR that introduces it.

### 5.5 Core query patterns

The six read patterns below reach their resolution target in three hops or
fewer from an indexed entry point. Alias text remains non-unique: resolve
queries therefore include `source_system` and return candidates with their
assertion identities instead of silently choosing a target.

Scope note: the identifier-resolve patterns (5.5.1, 5.5.2) return candidate
variant IDs plus assertion identity — deliberately not the full resolve
response. The component-expansion query (engine, body, platform, family,
manufacturer for a resolved variant) is owned by the resolve API work
(Phase 1 plan, Story 9.3) and will be added here as another executable
query block when that story lands. Do not treat 5.5.1 as the complete API
contract.

| Pattern | Indexed entry dependency (SCRUM-15) | Hops to result |
|---|---|---|
| Plate resolve | `Alias(source_system, alias_type, alias_text)` | 1 |
| k-type resolve | `Alias(source_system, alias_type, alias_text)` | 1 |
| Sibling amortization | unique `VehicleVariant.id` | 2 |
| Structured-form search | `ModelFamily.canonical_name`, `Manufacturer.canonical_name` | 3 to engine-filtered variant |
| Conflict lookup | `Alias.alias_text` | 1 |
| Gap detection | unique `VehicleVariant.id` | 1 |

#### 5.5.1 Plate resolve

Input: normalized `$alias_text` and `$source_system`. Output: every live,
customer-visible candidate plus its stable assertion identity. The service
returns a single resolution only when the candidate set is unambiguous.

<!-- query:plate_resolve:start -->
```cypher
MATCH (a:Alias {
  source_system: $source_system,
  alias_type: "plate",
  alias_text: $alias_text
})-[:REFERS_TO]->(v:VehicleVariant)
WHERE NOT v:Provisional AND NOT v:Superseded
RETURN a.id AS alias_id,
       a.source_assertion_key AS assertion_key,
       a.confidence AS confidence,
       v.id AS variant_id
ORDER BY confidence DESC, assertion_key
```
<!-- query:plate_resolve:end -->

Expected for `transportstyrelsen` / `ABC123`: one candidate, `VEH-07G`.

#### 5.5.2 k-type resolve

Input and output match plate resolve, with `source_system = "tecdoc"` and an
`alias_type` of `k_type`.

<!-- query:k_type_resolve:start -->
```cypher
MATCH (a:Alias {
  source_system: $source_system,
  alias_type: "k_type",
  alias_text: $alias_text
})-[:REFERS_TO]->(v:VehicleVariant)
WHERE NOT v:Provisional AND NOT v:Superseded
RETURN a.id AS alias_id,
       a.source_assertion_key AS assertion_key,
       a.confidence AS confidence,
       v.id AS variant_id
ORDER BY confidence DESC, assertion_key
```
<!-- query:k_type_resolve:end -->

Expected for `tecdoc` / `13902`: one candidate, `VEH-07G`.

#### 5.5.3 Sibling amortization

Input: `$variant_id`. Output: other visible variants sharing its engine. The
explicit inequality prevents the input variant from appearing as its own
sibling.

<!-- query:sibling_amortization:start -->
```cypher
MATCH (v:VehicleVariant {id: $variant_id})-[:USES_ENGINE]->(e:Engine)
      <-[:USES_ENGINE]-(sibling:VehicleVariant)
WHERE sibling.id <> v.id
  AND NOT sibling:Provisional
  AND NOT sibling:Superseded
WITH e, sibling ORDER BY sibling.id
RETURN e.engine_code AS engine_code,
       collect(DISTINCT sibling.id) AS sibling_variant_ids
```
<!-- query:sibling_amortization:end -->

Expected for `VEH-07G`: engine `OM642` and sibling `VEH-15P`; provisional
`VEH-08H` is excluded.

#### 5.5.4 Structured-form search

Input: `$make`, `$model`, `$year`, and `$fuel`. Output: visible variants and
their resolved engines. Starting at the indexed family, the path to the
engine-filtered variant is three hops.

<!-- query:structured_form_search:start -->
```cypher
MATCH (f:ModelFamily {canonical_name: $model})-[:MADE_BY]->
      (m:Manufacturer {canonical_name: $make})
MATCH (platform:Platform)-[:PLATFORM_OF]->(f)
MATCH (v:VehicleVariant)-[:BUILT_ON]->(platform)
MATCH (v)-[:USES_ENGINE]->(e:Engine {fuel_type: $fuel})
WHERE v.year_from <= $year
  AND (v.year_to IS NULL OR v.year_to >= $year)
  AND NOT v:Provisional
  AND NOT v:Superseded
RETURN v.id AS variant_id, e.id AS engine_id
ORDER BY variant_id
```
<!-- query:structured_form_search:end -->

Expected for Mercedes-Benz / E-Class / 2011 / diesel: `VEH-07G` and
`VEH-15P`.

#### 5.5.5 Conflict lookup

Input: normalized `$alias_text`. Output: source assertions only when identical
text points at more than one live canonical target.

<!-- query:conflict_lookup:start -->
```cypher
MATCH (a:Alias {alias_text: $alias_text})-[:REFERS_TO]->(target)
WHERE NOT target:Superseded
WITH a.alias_text AS alias_text,
     collect(DISTINCT target.id) AS target_ids,
     collect(DISTINCT {
       alias_id: a.id,
       source_system: a.source_system,
       assertion_key: a.source_assertion_key,
       target_id: target.id,
       confidence: a.confidence
     }) AS assertions
WHERE size(target_ids) > 1
RETURN alias_text, target_ids, assertions
```
<!-- query:conflict_lookup:end -->

Expected for `E350`: targets `VEH-07G` and `VEH-15P`, with each assertion's
source and stable assertion key retained.

#### 5.5.6 Gap detection

Input: an indexed batch of `$variant_ids`. Output: complete, non-provisional
variants missing any required structural relationship.

<!-- query:gap_detection:start -->
```cypher
UNWIND $variant_ids AS variant_id
MATCH (v:VehicleVariant {id: variant_id})
WHERE NOT v:Provisional
  AND NOT v:Superseded
  AND (
    NOT EXISTS { MATCH (v)-[:BUILT_ON]->(:Platform) }
    OR NOT EXISTS { MATCH (v)-[:USES_ENGINE]->(:Engine) }
    OR NOT EXISTS { MATCH (v)-[:USES_TRANSMISSION]->(:Transmission) }
    OR NOT EXISTS { MATCH (v)-[:HAS_BODY]->(:BodyType) }
  )
RETURN v.id AS variant_id
ORDER BY variant_id
```
<!-- query:gap_detection:end -->

The integration fixture includes `VEH-GAP`; an input batch containing it and
`VEH-07G` returns only `VEH-GAP`.

## 6. Examples

A real shared-component cluster: Mercedes E 350 CDI (W212, Sweden) sharing
its engine with the ML 350 CDI. IDs are shortened for readability —
illustrative only, invalid for real writes (see §1).

```
(mfr01:Manufacturer  {id: "MFR-01A", canonical_name: "Mercedes-Benz", country: "DE"})
(fam02b:ModelFamily  {id: "FAM-02B", canonical_name: "E-Class", segment: "executive"})
(fam02c:ModelFamily  {id: "FAM-02C", canonical_name: "M-Class", segment: "suv"})
(plt03:Platform      {id: "PLT-03C", platform_code: "W212", generation: "4",
                 year_from: 2009, year_to: 2016, facelift: false})
(plt16:Platform      {id: "PLT-16Q", platform_code: "W164", generation: "2",
                 year_from: 2005, year_to: 2011, facelift: false})
(eng04:Engine   {id: "ENG-04D", engine_code: "OM642", displacement_cc: 2987,
                 fuel_type: "diesel", configuration: "V6"})
(trn05:Transmission  {id: "TRN-05E", transmission_code: "722.9",
                 canonical_name: "7G-TRONIC", type: "automatic", gears: 7})
(bdy06:BodyType      {id: "BDY-06F", canonical_name: "sedan", door_count: 4})
(bdy17:BodyType      {id: "BDY-17R", canonical_name: "suv", door_count: 5})

(veh07:VehicleVariant {id: "VEH-07G", market: ["SE", "DE"], trim_level: "Avantgarde",
                  drive_type: "rwd", year_from: 2009, year_to: 2013})   // E 350 CDI sedan
(veh08:VehicleVariant:Provisional
                 {id: "VEH-08H", market: ["SE"], trim_level: null,
                  drive_type: "awd", year_from: 2009, year_to: 2011})   // ML 350 CDI, single-source
(veh15:VehicleVariant {id: "VEH-15P", market: ["DE"], trim_level: "Avantgarde",
                  drive_type: "awd", year_from: 2009, year_to: 2013})   // E 350 CDI 4MATIC

(ali09:Alias {id: "ALI-09I", alias_text: "13902", alias_type: "k_type",
         source_system: "tecdoc",
         source_record_key: "vehicle-82931",
         source_assertion_key: "vehicle-82931:k_type:0",
         confidence: 1.0})
(ali10:Alias {id: "ALI-10J", alias_text: "ABC123", alias_type: "plate",
         source_system: "transportstyrelsen",
         source_record_key: "vehicle-abc123",
         source_assertion_key: "vehicle-abc123:plate:0",
         confidence: 0.97})
(ali11:Alias {id: "ALI-11K", alias_text: "OM642", alias_type: "engine_code",
         source_system: "tecdoc",
         source_record_key: "engine-642",
         source_assertion_key: "engine-642:engine_code:0",
         confidence: 1.0})

// Same source, identical text, different targets: two TecDoc records both
// expose the model name "E350" — one for the sedan, one for another variant.
// Distinct assertion keys keep them apart; text alone never merges them.
(ali12:Alias {id: "ALI-12L", alias_text: "E350", alias_type: "model_name",
         source_system: "tecdoc",
         source_record_key: "vehicle-82931",
         source_assertion_key: "vehicle-82931:model_name:0",
         confidence: 0.92})
(ali13:Alias {id: "ALI-13M", alias_text: "E350", alias_type: "model_name",
         source_system: "tecdoc",
         source_record_key: "vehicle-82940",
         source_assertion_key: "vehicle-82940:model_name:0",
         confidence: 0.90})

// Identical text asserted by a second, independent source: its own Alias
// node with its own identity and confidence.
(ali14:Alias {id: "ALI-14N", alias_text: "E350", alias_type: "model_name",
         source_system: "transportstyrelsen",
         source_record_key: "vehicle-abc123",
         source_assertion_key: "vehicle-abc123:model_name:0",
         confidence: 0.81})

// Exactly one outgoing REFERS_TO edge per Alias; every target is live.
(ali09)-[:REFERS_TO]->(veh07)
(ali10)-[:REFERS_TO]->(veh07)
(ali11)-[:REFERS_TO]->(eng04)
(ali12)-[:REFERS_TO]->(veh07)
(ali13)-[:REFERS_TO]->(veh15)
(ali14)-[:REFERS_TO]->(veh07)

// Canonical hierarchy and shared-component relationships (§5.2).
(fam02b)-[:MADE_BY]->(mfr01)
(fam02c)-[:MADE_BY]->(mfr01)
(plt03)-[:PLATFORM_OF]->(fam02b)
(plt16)-[:PLATFORM_OF]->(fam02c)
(veh07)-[:BUILT_ON]->(plt03)
(veh08)-[:BUILT_ON]->(plt16)
(veh15)-[:BUILT_ON]->(plt03)
(veh07)-[:USES_ENGINE {power_kw: 170, torque_nm: 540,
                       emission_standard: "Euro 5"}]->(eng04)
(veh08)-[:USES_ENGINE {power_kw: 165, torque_nm: 510,
                       emission_standard: "Euro 5"}]->(eng04)
(veh15)-[:USES_ENGINE {power_kw: 170, torque_nm: 540,
                       emission_standard: "Euro 5"}]->(eng04)
(veh07)-[:USES_TRANSMISSION]->(trn05)
(veh08)-[:USES_TRANSMISSION]->(trn05)
(veh15)-[:USES_TRANSMISSION]->(trn05)
(veh07)-[:HAS_BODY]->(bdy06)
(veh08)-[:HAS_BODY]->(bdy17)
(veh15)-[:HAS_BODY]->(bdy06)
```

What the example demonstrates:

- **Shared component:** all three variants hold one `USES_ENGINE` edge to the
  single `ENG-04D`; installation-specific output lives on those edges, which
  is why `Engine` has no power property.
- **Connected vocabulary:** `MADE_BY` and `PLATFORM_OF` provide the only path
  from a variant's platform to its family and manufacturer. Transmission and
  body nodes are connected without duplicating those hierarchy facts.
- **Dual-alias pattern:** the k-type alias targets the VehicleVariant; the
  engine-code alias targets the Engine directly.
- **Duplicate text, stable identity:** `ALI-12L` and `ALI-13M` share the text
  `"E350"` within the same source but map to `VEH-07G` and `VEH-15P`;
  `ALI-14N` shows the same text from another source. Identity is
  `(source_system, source_assertion_key)`, never the text.
- **Single live target:** every Alias has exactly one explicit outgoing
  `REFERS_TO` edge, and each edge targets a live canonical node.
- **Normalization correction:** if Stage 1a later normalizes `"E 350"` to
  `"E350"`, the affected Alias keeps its assertion key and its `alias_text`
  is updated in place — no duplicate Alias is minted.
- **Provisional lifecycle:** `VEH-08H` exists only from Transportstyrelsen
  data (single source, 0.65–0.90 band), so it is `:Provisional` and excluded
  from customer resolves until confirmed.
- **No name on VehicleVariant:** "Mercedes-Benz E 350 CDI Avantgarde" is
  assembled by traversal, never stored.

## 7. Opaque ID generation (SCRUM-14)

### 7.1 Canonical format and prefixes

Every persisted graph node ID has exactly 30 characters:

```text
<three-character prefix>-<26-character ULID>
ENG-01ARZ3NDEKTSV4RRFFQ69G5FAV
```

The ULID payload is the uppercase canonical Crockford Base32 alphabet
`0123456789ABCDEFGHJKMNPQRSTVWXYZ`. Lowercase payloads and the ambiguous
characters `I`, `L`, `O`, and `U` are invalid. The leading ULID character is
limited to `0` through `7` so the payload fits exactly 128 bits.

| Node label | Prefix | Example shape |
|---|---|---|
| `Manufacturer` | `MFR` | `MFR-<ULID>` |
| `ModelFamily` | `FAM` | `FAM-<ULID>` |
| `Platform` | `PLT` | `PLT-<ULID>` |
| `Engine` | `ENG` | `ENG-<ULID>` |
| `Transmission` | `TRN` | `TRN-<ULID>` |
| `BodyType` | `BDY` | `BDY-<ULID>` |
| `VehicleVariant` | `VEH` | `VEH-<ULID>` |
| `Alias` | `ALI` | `ALI-<ULID>` |

Prefixes communicate canonical node type only. They never identify a source,
tenant, country, environment, or ingestion job.

### 7.2 ULID construction and collision model

The shared generator builds the 128-bit ULID payload from:

- a 48-bit Unix timestamp in milliseconds; and
- 80 bits of cryptographically secure random entropy.

The timestamp makes ULID payloads, and full IDs with the same prefix,
lexicographically sortable across different milliseconds. Generation is
intentionally stateless and does not promise a stable ordering among IDs
minted within the same millisecond. The 80-bit random field provides
approximately 1.2 x 10^24 possible values per millisecond; the Story 2.4
database uniqueness constraints remain the final integrity backstop.

ULID was selected over a source-derived key or plain UUID because it combines
source independence, a compact unambiguous uppercase representation,
operationally useful time ordering, and an explicit node-type prefix. The
timestamp is not a business creation date and must not replace `created_at`.

### 7.3 Shared utility contract

`northstar.node_ids` is the only application utility that may mint, parse, or
validate canonical graph IDs. It is a pure shared module usable by ingestion
and future API graph-write paths.

```python
from northstar.node_ids import NodeIdGenerator, NodeIdPrefix, parse_node_id

generator = NodeIdGenerator()
vehicle_id = generator.mint(NodeIdPrefix.VEHICLE_VARIANT)
parsed = parse_node_id(vehicle_id)

assert parsed.prefix is NodeIdPrefix.VEHICLE_VARIANT
```

Public behavior:

- `NodeIdGenerator.mint(prefix)` accepts only one of the eight canonical
  prefixes and returns a new 30-character ID.
- `mint_node_id(prefix)` is the production convenience function using the
  system clock and cryptographically secure entropy.
- `parse_node_id(value)` rejects malformed, lowercase, unknown-prefix, and
  overflowing values and returns the typed prefix, ULID, and timestamp.
- `is_valid_node_id(value)` provides a non-raising validation predicate.
- Tests may inject a clock and entropy source into `NodeIdGenerator`; business
  code must use the secure defaults.

### 7.4 Lookup before minting

Generation provides unique IDs, but it does not provide ingestion
idempotency. Every graph write path must look up and reconcile existing
identity before minting:

```text
1. Look up (source_system, source_assertion_key).
2. Reuse the existing Alias and canonical target when present.
3. Follow any accepted plate-to-k-type mapping to an existing variant.
4. Reconcile remaining canonical candidates.
5. Mint only when no canonical match exists.
```

When a plate and k-type are already connected, they remain separate source
assertions with separate `ALI-<ULID>` IDs and share one `VEH-<ULID>` target:

```text
(plate_alias:Alias {id: "ALI-01ARZ3NDEKTSV4RRFFQ69G5FAV", alias_type: "plate",
                     alias_text: "ABC123",
                     source_system: "transportstyrelsen"})
  -[:REFERS_TO]->(variant:VehicleVariant {
      id: "VEH-01ARZ3NDEKTSV4RRFFQ69G5FAX"
    })

(k_type_alias:Alias {id: "ALI-01ARZ3NDEKTSV4RRFFQ69G5FAW", alias_type: "k_type",
                      alias_text: "13902", source_system: "tecdoc"})
  -[:REFERS_TO]->(variant)
```

`ABC123` and `13902` never become node IDs. Re-importing either source
assertion reuses its existing Alias and target rather than minting another
canonical variant.

### 7.5 Lifecycle and ownership boundaries

- IDs are immutable and never recycled.
- Correcting `alias_text` does not change the Alias ID.
- A source-system rename does not change existing canonical IDs.
- IDs may appear in APIs, stable URLs, logs, audits, integrations, exports,
  and support tooling. Product stories decide whether they appear in primary
  user-facing screens.
- Duplicate reconciliation, old-ID redirects, merge reversal, and
  `:Superseded` lifecycle mechanics remain owned by SCRUM-68. They do not
  change the no-reuse rule established here.
- Database uniqueness constraints and migrations remain owned by SCRUM-15.

## 8. Schema PR review checklist

Every PR that touches this schema (or code writing to the graph) must be
checked against:

- [ ] Every node keyed by an opaque `<PREFIX>-<ULID>` `id`; no external code
      used as an internal ID anywhere.
- [ ] No new property duplicates a fact owned by an edge or a neighboring
      node (especially: no power/torque on Engine, no assembled display names
      on VehicleVariant, no `target_node_id` on Alias).
- [ ] Every new property has an explicit type from §2 and a
      required/nullable decision.
- [ ] Enum-like values are either in the §2/§3 enum lists or added to the
      Epic 4 dictionaries in the same PR.
- [ ] k-type appears only as an Alias (`alias_type: "k_type"`), never as a
      label or a VehicleVariant property.
- [ ] Alias identity is `(source_system, source_assertion_key)`; identity
      fields are never mutated, and `alias_text` stays a non-unique lookup
      value.
- [ ] Every Alias has exactly one outgoing `REFERS_TO` edge to a live
      (non-`:Superseded`) node.
- [ ] New-node write paths set `:Provisional` for the 0.65–0.90 confidence
      band and record provenance to the enrichment ledger.
- [ ] IDs are never reused; merged-away nodes become `:Superseded`, not
      deleted.
- [ ] New graph write paths use `northstar.node_ids` and perform lookup and
      reconciliation before minting.
- [ ] Year ranges use `year_from`/`year_to` with `null` = current; no other
      date encodings.
- [ ] Every relationship follows the §5.2 direction and appears in its
      catalog; new names are added there in the same PR.
- [ ] Cardinality expectations from §5.3 hold; no inverse or parallel
      duplicate domain edge is written for read convenience or provenance.
- [ ] Complete variants have exactly one `BUILT_ON`, `USES_ENGINE`,
      `USES_TRANSMISSION`, and `HAS_BODY` target; only `:Provisional` variants
      may temporarily omit one.
- [ ] Pairing-specific facts (power, torque, emission standard) are on
      edges, never copied onto component nodes.
