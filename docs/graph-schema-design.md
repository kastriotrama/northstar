# Graph Schema Design — Nodes & Relationships

| Field | Value |
|---|---|
| Version | `0.2` |
| Status | Draft node and relationship contract |
| Owner | NorthStar backend team |
| Jira story | SCRUM-12 (nodes), SCRUM-13 (relationships) |
| Scope | Node labels, properties, relationships, cardinality, query patterns, examples, and invariants |
| Last reviewed | 2026-07-14 |

Canonical graph model for the Neo4j knowledge graph (Phase 1, Stories 2.1
and 2.2). This document is canonical but incomplete until later Epic 2
stories extend it.

Relationship names, direction, cardinality, and edge properties are defined
in §5 (SCRUM-13). ID minting, constraints/indexes, and merge/split mechanics
remain owned by their stories per the table below.

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
   payload and generation utility are finalized by SCRUM-14. Shortened IDs in
   diagrams and examples (e.g. `ENG-04D`) are illustrative only and invalid
   for real writes.
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

### 5.1 Direction rule

Edges always point **from the more specific thing to the more general or
shared thing**: variant → component, alias → target, platform → family,
family → manufacturer. One rule, no exceptions — writers and reviewers never
have to guess direction, and traversals from the resolution target
(VehicleVariant) read outward naturally.

Invalid by construction (write paths must reject):

```text
(Engine)-[:USES_ENGINE]->(VehicleVariant)      // inverted direction
(alias)-[:REFERS_TO]->(a), (alias)-[:REFERS_TO]->(b)   // two targets for one Alias
(veh)-[:MEMBER_OF]->(f1), (veh)-[:MEMBER_OF]->(f2)     // two families for one variant
```

### 5.2 Relationship catalog

| Relationship | Start → End | Cardinality | Edge properties | Intent |
|---|---|---|---|---|
| `USES_ENGINE` | VehicleVariant → Engine | each variant has exactly 1 engine; an engine serves many variants | `power_hp` (int, nullable), `torque_nm` (int, nullable), `emission_std` (string, nullable), `year_from`/`year_to` (year, nullable), `source` (enum, required), `confidence` (float, required) | The installation-specific tune of a shared engine design |
| `HAS_TRANSMISSION` | VehicleVariant → Transmission | 0..1 per variant (0 = not yet known); a transmission serves many variants | `source`, `confidence` | Which transmission design the variant ships with |
| `BUILT_ON` | VehicleVariant → Platform | exactly 1 per variant; a platform carries many variants | `source`, `confidence` | Chassis/generation membership |
| `HAS_BODY` | VehicleVariant → BodyType | exactly 1 per variant | `source`, `confidence` | Body style |
| `MEMBER_OF` | VehicleVariant → ModelFamily | exactly 1 per variant | `source`, `confidence` | The family users search by |
| `BELONGS_TO` | Platform → ModelFamily | 1 per platform (see tradeoff note) | `source`, `confidence` | Positions the generation inside the family |
| `MADE_BY` | ModelFamily → Manufacturer | exactly 1 per family | `source`, `confidence` | Brand ownership |
| `REFERS_TO` | Alias → any canonical node | exactly 1 outgoing per Alias, always to a live node | none — identity and confidence live on the Alias node | The single source of alias-to-node mapping |
| `SUPERSEDED_BY` | VehicleVariant → VehicleVariant | 0..1 outgoing per superseded node | owned by SCRUM-68 | Merge trail; targets of retired nodes stay resolvable |

**Cardinality semantics:** "exactly 1" is the *logical* expectation enforced
by the write path and validated by data-quality jobs (§5.4 pattern 6).
During conflict handling, **parallel assertions are permitted**: two sources
may assert `USES_ENGINE` to the same Engine with different `power_hp` — both
edges are kept, each carrying its own `source` and `confidence`, and the
conflict is flagged rather than auto-resolved (Phase 1 plan, Story 6.3). A
resolve response uses the highest-confidence assertion; enrichment resolves
the conflict later.

**Edge property conventions:** assertion edges carry `source`
(enum(`tecdoc`, `transportstyrelsen`, `manual`)) and `confidence` (float
0.0–1.0), same types and semantics as on Alias. The intrinsic-vs-pairing
rule from §1 decides node vs edge placement: intrinsic to the component →
node property; specific to the pairing → edge property.

### 5.3 Naming conventions and tradeoffs

- Names are `UPPER_SNAKE_CASE`, verb phrases read from the start node:
  *variant USES engine*, *alias REFERS TO target*.
- Known inconsistency, accepted deliberately: `USES_ENGINE` vs
  `HAS_TRANSMISSION`/`HAS_BODY`. The names come from the Phase 1 plan and
  are kept stable rather than renamed for symmetry — renaming after
  downstream code exists costs more than the aesthetic gain. `USES_` also
  signals the one edge that carries heavy pairing facts.
- `BELONGS_TO` tradeoff: some real platforms span families (e.g. VW MQB).
  Phase 1 models a platform under its primary family; if TecDoc data
  contradicts this at load time, the decision is revisited in SCRUM-68's
  split procedure rather than pre-engineered now.
- New relationship names must be added to the catalog table in the same PR
  that introduces them (checklist item in §7).

### 5.4 Core query patterns

The six read patterns the schema must serve (Phase 1 plan, Story 2.2). Each
resolves in ≤3 hops from an indexed entry point (`Alias.alias_text`,
`ModelFamily.canonical_name`, `Manufacturer.canonical_name`, or an internal
id). Indexes are implemented by SCRUM-15.

**1. Plate resolve** — input: normalized plate text; output: the variant and
its component structure.

```cypher
MATCH (a:Alias {alias_type: "plate", alias_text: $plate})-[:REFERS_TO]->(v:VehicleVariant)
WHERE NOT v:Provisional AND NOT v:Superseded
OPTIONAL MATCH (v)-[ue:USES_ENGINE]->(e:Engine)
OPTIONAL MATCH (v)-[:HAS_BODY]->(b:BodyType)
OPTIONAL MATCH (v)-[:MEMBER_OF]->(f:ModelFamily)-[:MADE_BY]->(m:Manufacturer)
RETURN v, e, ue, b, f, m
```

Entry: `Alias.alias_text` index; deepest path alias→variant→family→
manufacturer = 3 hops. The response's k-type comes from the reverse alias
lookup: `MATCH (kt:Alias {alias_type: "k_type"})-[:REFERS_TO]->(v)`.

Expected result for `$plate = "ABC123"` against the §6 example data:
`VEH-07G` with engine `OM642` (231 hp assertion), body `sedan`, family
`E-Class`, manufacturer `Mercedes-Benz`.

**2. k-type resolve** — input: k-type text; output: same shape as pattern 1.

```cypher
MATCH (a:Alias {alias_type: "k_type", alias_text: $k_type})-[:REFERS_TO]->(v:VehicleVariant)
WHERE NOT v:Provisional AND NOT v:Superseded
RETURN v   // component expansion identical to pattern 1
```

Expected result for `$k_type = "13902"` against the §6 example data:
`VEH-07G` (same component structure as pattern 1).

**3. Sibling amortization** — input: variant id; output: all variants
sharing its engine (the "40 variants share one OM642" payoff).

```cypher
MATCH (:VehicleVariant {id: $variant_id})-[:USES_ENGINE]->(e:Engine)
      <-[:USES_ENGINE]-(sib:VehicleVariant)
WHERE NOT sib:Provisional AND NOT sib:Superseded
RETURN e.engine_code, collect(DISTINCT sib.id) AS sibling_variant_ids
```

Entry: internal id constraint; 2 hops.

Expected result for `$variant_id = "VEH-07G"` against the §6 example data:
engine `OM642` with `sibling_variant_ids = ["VEH-15P"]`. The `:Provisional`
`VEH-08H` is filtered out here; enrichment tooling that drops the
`Provisional` filter sees `["VEH-08H", "VEH-15P"]`.

**4. Structured-form search** — input: make + model + year + fuel; output:
ranked candidate variants.

```cypher
MATCH (m:Manufacturer {canonical_name: $make})<-[:MADE_BY]-
      (f:ModelFamily {canonical_name: $model})<-[:MEMBER_OF]-(v:VehicleVariant)
WHERE v.year_from <= $year AND (v.year_to IS NULL OR v.year_to >= $year)
  AND NOT v:Provisional AND NOT v:Superseded
MATCH (v)-[:USES_ENGINE]->(e:Engine {fuel_type: $fuel})
RETURN v, e
```

Entry: `canonical_name` indexes; 3 hops. Ranking (by assertion confidence
and match quality) happens in the service layer, not in Cypher.

Expected result for `$make = "Mercedes-Benz"`, `$model = "E-Class"`,
`$year = 2011`, `$fuel = "diesel"` against the §6 example data: two
candidates, `VEH-07G` and `VEH-15P` (both E-Class diesels in production in
2011); the service ranks them for the caller.

**5. Conflict lookup** — input: variant id; output: attributes with
disagreeing parallel assertions, for the review workflow.

```cypher
MATCH (v:VehicleVariant {id: $variant_id})-[r:USES_ENGINE]->(e:Engine)
WITH e, collect({source: r.source, power_hp: r.power_hp,
                 confidence: r.confidence}) AS assertions
WHERE size(assertions) > 1
RETURN e.id AS engine_id, assertions
```

Entry: internal id; 1 hop. The service decides whether multiple assertions
actually disagree; the graph just surfaces them.

Expected result for `$variant_id = "VEH-07G"` against the §6 example data:
`engine_id = "ENG-04D"` with two assertions — 231 hp (`tecdoc`, 1.0) and
235 hp (`transportstyrelsen`, 0.78) — a real disagreement for the review
queue.

**6. Gap detection** — input: none (batch); output: variants missing an
expected structural edge.

```cypher
MATCH (v:VehicleVariant)
WHERE NOT v:Superseded AND NOT (v)-[:HAS_TRANSMISSION]->(:Transmission)
RETURN v.id
LIMIT $batch_limit
```

1 hop, but label-scan based — this is a scheduled data-quality job (Epic
10.2 coverage report), never an API request path.

Expected result against the §6 example data: `VEH-08H` and `VEH-15P` (only
`VEH-07G` has a `HAS_TRANSMISSION` edge) — both are transmission-coverage
gaps for the quality report.

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
(eng04:Engine   {id: "ENG-04D", engine_code: "OM642", displacement_cc: 2987,
                 fuel_type: "diesel", configuration: "V6"})
(trn05:Transmission  {id: "TRN-05E", transmission_code: "722.9",
                 canonical_name: "7G-TRONIC", type: "automatic", gears: 7})
(bdy06:BodyType      {id: "BDY-06F", canonical_name: "sedan", door_count: 4})

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

// Structural edges (§5.2): specific -> general, pairing facts on the edge.
(veh07)-[:USES_ENGINE {power_hp: 231, torque_nm: 540, emission_std: "Euro 5",
                       source: "tecdoc", confidence: 1.0}]->(eng04)
(veh08)-[:USES_ENGINE {power_hp: 224, torque_nm: 510, emission_std: "Euro 5",
                       source: "transportstyrelsen", confidence: 0.81}]->(eng04)
(veh15)-[:USES_ENGINE {power_hp: 231, torque_nm: 540, emission_std: "Euro 5",
                       source: "tecdoc", confidence: 1.0}]->(eng04)

// Parallel assertion on the same pairing: a second source disagrees on the
// tune. Both edges are kept, flagged as a conflict (query pattern 5) — never
// auto-resolved at write time.
(veh07)-[:USES_ENGINE {power_hp: 235, torque_nm: null, emission_std: null,
                       source: "transportstyrelsen", confidence: 0.78}]->(eng04)

(veh07)-[:HAS_TRANSMISSION {source: "tecdoc", confidence: 1.0}]->(trn05)
(veh07)-[:BUILT_ON {source: "tecdoc", confidence: 1.0}]->(plt03)
(veh07)-[:HAS_BODY {source: "tecdoc", confidence: 1.0}]->(bdy06)
(veh07)-[:MEMBER_OF {source: "tecdoc", confidence: 1.0}]->(fam02b)
(veh15)-[:MEMBER_OF {source: "tecdoc", confidence: 1.0}]->(fam02b)
(plt03)-[:BELONGS_TO {source: "tecdoc", confidence: 1.0}]->(fam02b)
(fam02b)-[:MADE_BY {source: "tecdoc", confidence: 1.0}]->(mfr01)
```

What the example demonstrates:

- **Shared component:** both variants hold `USES_ENGINE` edges to the single
  `ENG-04D`; the E 350's 231 hp and the ML 350's 224 hp tunes live on those
  edges, which is why `Engine` has no power property.
- **Conflict as parallel assertions:** `veh07` carries two `USES_ENGINE`
  assertions to the same engine (231 hp from TecDoc, 235 hp from
  Transportstyrelsen). Both are kept with their own `source` and
  `confidence`; query pattern 5 surfaces the disagreement for review instead
  of a writer silently picking a winner.
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

## 7. Schema PR review checklist

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
- [ ] Year ranges use `year_from`/`year_to` with `null` = current; no other
      date encodings.
- [ ] Every relationship follows the §5.1 direction rule
      (specific → general) and appears in the §5.2 catalog; new relationship
      names are added to the catalog in the same PR.
- [ ] Cardinality expectations from §5.2 hold; parallel assertions are only
      used for source conflicts and always carry `source` + `confidence`.
- [ ] Pairing-specific facts (power, torque, emission standard) are on
      edges, never copied onto component nodes.
