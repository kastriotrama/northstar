# Graph Schema Design — Node Labels & Properties

Canonical node model for the Neo4j knowledge graph (Phase 1, Story 2.1 /
SCRUM-12). Relationship types and edge properties are Story 2.2 and will be
added to this document by that story; where an example below needs an edge, it
uses the relationship names from the Phase 1 plan (`docs/PHASE_1_PLAN.md`).

## 1. Core principles

1. **Opaque internal IDs.** Every node is keyed by an internal ID that carries
   no source meaning: `<PREFIX>-<ULID>` (e.g. `ENG-01J1QYVN4T9GZ0`). External
   codes (TecDoc k-type, engine codes, plates, VINs) are NEVER node IDs — they
   enter the graph only as `Alias` nodes. IDs are never reused, including
   after node merges or splits.
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
| `id` | string | required, unique per label | `<PREFIX>-<ULID>`, minted by the central ID utility (Story 2.3) |
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
live on the `USES_ENGINE` edge (Story 2.2).

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
| `market` | string[] | required | `["SE", "DE"]` | ISO 3166-1 alpha-2 markets where sold; grows as sources confirm |
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
| `alias_text` | string | required | `"ABC123"` | Normalized form (Epic 4 Stage 1a) of the external string |
| `source_system` | enum(`tecdoc`, `transportstyrelsen`, `plate`, `vin`, `manual`) | required | `"plate"` | Where this vocabulary comes from; new sources extend the enum |
| `external_code` | string | nullable | `"12345"` | The source's own identifier when distinct from `alias_text` (e.g. TecDoc k-type number for a display string) |
| `confidence` | float | required | `0.97` | 0.0–1.0 confidence of the mapping, from the Epic 4 scoring gate |

**Rules (deliberate, load-bearing):**

- **No `target_node_id` property.** The `REFERS_TO` edge is the single source
  of the alias-to-node mapping. A property copy would diverge from the edge
  the first time a node merge re-points edges (Story 2.5) and silently
  corrupt resolution. Uniqueness of the mapping = each Alias has exactly one
  outgoing `REFERS_TO` edge to a live node.
- **k-type is always an Alias**, never a label or a property on
  VehicleVariant. One variant may map to multiple k-types (TecDoc splits
  finer than we do in places). Dual-alias pattern: k-type aliases point at
  VehicleVariant nodes; TecDoc engine/body codes point at Engine/BodyType
  nodes directly.
- Aliases are per-source: the same text `"E350"` from TecDoc and from a
  clerk-typed Transportstyrelsen field are two Alias nodes with different
  `source_system` and confidence.

## 4. `:Provisional` secondary label

Nodes created from records scoring 0.65–0.90 in the normalization gate carry
`:Provisional` in addition to their primary label (e.g.
`:VehicleVariant:Provisional`).

- Excluded from customer-facing resolves by default (resolve queries filter
  `NOT n:Provisional`).
- Promoted by removing the label when a second independent source confirms
  the node; demotion/merge handled by the Story 2.5 merge procedure.
- `:Provisional` carries no properties of its own; the confidence that put it
  there lives on the Alias/edge that created it and in the enrichment ledger.

Nodes retired by a merge receive `:Superseded` plus a `SUPERSEDED_BY` edge
(Story 2.5); they are never deleted, so ledger rows stay resolvable.

## 5. Examples

A real shared-component cluster: Mercedes E 350 CDI (W212, Sweden) sharing
its engine with the ML 350 CDI. IDs shortened for readability.

```
(:Manufacturer  {id: "MFR-01A", canonical_name: "Mercedes-Benz", country: "DE"})
(:ModelFamily   {id: "FAM-02B", canonical_name: "E-Class", segment: "executive"})
(:ModelFamily   {id: "FAM-02C", canonical_name: "M-Class", segment: "suv"})
(:Platform      {id: "PLT-03C", platform_code: "W212", generation: "4",
                 year_from: 2009, year_to: 2016, facelift: false})
(:Engine        {id: "ENG-04D", engine_code: "OM642", displacement_cc: 2987,
                 fuel_type: "diesel", configuration: "V6"})
(:Transmission  {id: "TRN-05E", transmission_code: "722.9",
                 canonical_name: "7G-TRONIC", type: "automatic", gears: 7})
(:BodyType      {id: "BDY-06F", canonical_name: "sedan", door_count: 4})

(:VehicleVariant {id: "VEH-07G", market: ["SE", "DE"], trim_level: "Avantgarde",
                  drive_type: "rwd", year_from: 2009, year_to: 2013})   // E 350 CDI sedan
(:VehicleVariant:Provisional
                 {id: "VEH-08H", market: ["SE"], trim_level: null,
                  drive_type: "awd", year_from: 2009, year_to: 2011})   // ML 350 CDI, single-source

(:Alias {id: "ALI-09I", alias_text: "13902", source_system: "tecdoc",
         external_code: "13902", confidence: 1.0})       // k-type -> VEH-07G
(:Alias {id: "ALI-10J", alias_text: "ABC123", source_system: "plate",
         external_code: null, confidence: 0.97})          // Swedish plate -> VEH-07G
(:Alias {id: "ALI-11K", alias_text: "OM642", source_system: "tecdoc",
         external_code: "642", confidence: 1.0})          // engine code -> ENG-04D
```

What the example demonstrates:

- **Shared component:** both variants will hold `USES_ENGINE` edges to the
  single `ENG-04D`; the E 350's 231 hp and the ML 350's 224 hp tunes belong
  on those edges, which is why `Engine` has no power property.
- **Dual-alias pattern:** the k-type alias targets the VehicleVariant; the
  engine-code alias targets the Engine directly.
- **Provisional lifecycle:** `VEH-08H` exists only from Transportstyrelsen
  data (single source, 0.65–0.90 band), so it is `:Provisional` and excluded
  from customer resolves until confirmed.
- **No name on VehicleVariant:** "Mercedes-Benz E 350 CDI Avantgarde" is
  assembled by traversal, never stored.

## 6. Schema PR review checklist

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
- [ ] k-type appears only as an Alias (`source_system: "tecdoc"`), never as
      a label or a VehicleVariant property.
- [ ] New-node write paths set `:Provisional` for the 0.65–0.90 confidence
      band and record provenance to the enrichment ledger.
- [ ] IDs are never reused; merged-away nodes become `:Superseded`, not
      deleted.
- [ ] Year ranges use `year_from`/`year_to` with `null` = current; no other
      date encodings.
