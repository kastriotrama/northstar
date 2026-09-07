# Graph Schema Design — Wheel & Tyre Fitment

| Field | Value |
|---|---|
| Version | `0.1` |
| Status | Proposed — extends `graph-schema-design.md` v0.4 |
| Scope | `TyreSpec`, `RimSpec`, `WheelFitment` nodes; fitment relationships; identity and invariants |
| Depends on | §2 type system, §3 node conventions, §5 relationship rules of the core schema |

## 1. Why this is not one node

The obvious design is a `Tyre` node hanging off `VehicleVariant`. It fails on
four counts, each of which is a real fitment we already hold.

**A fitment is a three-way fact.** It binds a variant, a tyre size *and* a rim.
Neo4j has no hyperedges, so two independent edges from the variant would lose
which tyre belongs on which rim. That is fatal for staggered cars: staging id
`990077810` is a GLK carrying `235/45 R20` front and `255/40 R20` rear. Two
loose edges say the car has two tyre sizes and two rim sizes, and cannot say
which pairs with which. **`WheelFitment` is therefore a reified relationship**,
the same move the core schema already makes for `Alias`.

**Size and requirement are different things.** `235/45R18 94W` is a dimension.
"Must be run-flat, must bear the Mercedes `MO` mark, 2.4 bar laden" is what
*this vehicle* demands of a tyre in that size. The dimension is shared by
thousands of variants and must deduplicate hard; the requirement is per
fitment. Mixing them multiplies the size node by every requirement combination.

**Approval marks are identity, not decoration.** A Porsche `N0` and `N1` in the
same size are different tyres and are not interchangeable — the carmaker
approved a specific carcass and compound. Same for Mercedes `MO` / `MOE`, BMW
`★`, Audi `AO` / `RO1`, Volvo `VOL`, Ferrari `K1`, Tesla `T0`. A model that
treats the mark as a label on a size cannot answer "what may I legally and
safely fit", which is the only question worth asking.

**Not every size is metric.** 14.2% of the 6.6M tyre strings in
`staging.transportstyrelsen_raw` are not `nnn/nn Rnn`: `5.60-15` (imperial),
`175SR14` (alpha, speed inside, no aspect ratio), `165-15`, `P235/75R15`
(P-metric). A schema with required `aspect_ratio` silently drops a million
rows. `size_system` plus nullable aspect keeps them.

## 2. Nodes

### 2.1 TyreSpec (`TYR-`)

One tyre **size**, deduplicated globally. Intrinsic dimensional facts only —
nothing about brand, season, or which car it fits.

| Property | Type | Required | Example | Notes |
|---|---|---|---|---|
| `canonical_code` | string | required | `"235/45R18 94W XL"` | Normalized single-spaced form; the node's natural key |
| `size_system` | enum(`metric`, `p_metric`, `lt_metric`, `alpha`, `imperial`, `flotation`, `trx`) | required | `"metric"` | Governs which dimensional fields are populated |
| `section_width_mm` | int | nullable | `235` | Null for imperial (`5.60-15`), where width is in inches |
| `section_width_in` | float | nullable | `5.60` | Populated only for `imperial` / `flotation` |
| `aspect_ratio` | int | nullable | `45` | Null for `185R14` and imperial; **not** defaultable to 82 |
| `construction` | enum(`radial`, `bias`, `belted_bias`) | required | `"radial"` | From `R` / `-` / `B` |
| `rim_diameter_in` | float | nullable | `18` | Float, not int — `16.5` and `17.5` are real |
| `rim_diameter_mm` | int | nullable | `390` | TRX and metric-rim sizes only |
| `load_index` | int | nullable | `94` | Single-fitment index |
| `load_index_dual` | int | nullable | `102` | Second value of `104/102` twin-wheel commercial sizes |
| `speed_symbol` | string | nullable | `"W"` | `Y`, `(Y)`, `ZR` retained verbatim |
| `load_range` | enum(`sl`, `xl`, `c`, `lt`, `d`, `e`) | nullable | `"xl"` | `C` is the commercial casing in `195/60 R16C 99H` — **not** part of rim diameter |
| `ply_rating` | int | nullable | `8` | LT sizes carrying `8PR` |
| `raw_variants` | string[] | required | `["235/45 R18 94W XL", "235/45R18 94W XL"]` | Every source spelling seen; feeds matching without re-parsing |

`canonical_code` carries a uniqueness constraint. `raw_variants` exists because
the registry writes the same size six ways, including glued forms with no
delimiter at all (`215/55R1794W` is `215/55 R17 94W`).

### 2.2 RimSpec (`RIM-`)

One wheel **specification**, deduplicated globally. A tyre size does not imply a
rim: `235/45R18` mounts on 7.5J, 8J and 8.5J, and the offset differs per car.

| Property | Type | Required | Example | Notes |
|---|---|---|---|---|
| `canonical_code` | string | required | `"8Jx18 ET45 5x112 CB66.6"` | Natural key |
| `diameter_in` | float | required | `18` | Must equal the tyre's `rim_diameter_in` in any fitment |
| `width_in` | float | required | `8.0` | |
| `flange_profile` | enum(`j`, `jj`, `k`, `b`, `p`, `d`) | nullable | `"j"` | The `J` in `8Jx18` |
| `offset_mm` | int | required | `45` | ET; **signed** — deep-dish and truck rims are negative |
| `bolt_count` | int | required | `5` | |
| `pitch_circle_diameter_mm` | float | required | `112` | The `112` in `5x112`; float for `4x100.1` |
| `centre_bore_mm` | float | nullable | `66.6` | Hub-centric fitment; wrong bore is a safety fault |
| `material` | enum(`steel`, `alloy`, `forged_alloy`, `carbon`) | nullable | `"alloy"` | Affects load rating and winter suitability |
| `fastener_type` | enum(`bolt`, `stud_nut`) | nullable | `"bolt"` | German makes bolt, Japanese studs |
| `fastener_thread` | string | nullable | `"M14x1.5"` | |
| `seat_type` | enum(`cone`, `ball`, `flat`) | nullable | `"cone"` | Mismatched seats shear studs |
| `load_rating_kg` | int | nullable | `690` | Per wheel |

### 2.3 WheelFitment (`WFT-`)

The reified fact: *this variant may run this tyre on this rim, on this axle,
under these conditions*. One node per approved configuration.

| Property | Type | Required | Example | Notes |
|---|---|---|---|---|
| `role` | enum(`standard`, `factory_option`, `approved_alternative`, `spare`, `space_saver`, `winter_package`) | required | `"standard"` | Distinguishes what shipped from what is merely permitted |
| `season` | enum(`summer`, `winter`, `all_season`, `any`) | required | `"any"` | `winter` fitments are frequently a *narrower* size on a smaller rim |
| `is_staggered` | bool | required | `true` | Derived; true when front and rear tyre differ |
| `requires_run_flat` | bool | required | `false` | Cars delivered without a spare mandate it |
| `requires_severe_snow` | bool | required | `false` | 3PMSF — statutory in Sweden 1 Dec–31 Mar |
| `homologation_mark` | string | nullable | `"MO"` | `MO`, `MOE`, `★`, `AO`, `RO1`, `N0`–`N6`, `VOL`, `J`, `LR`, `K1`, `T0`. Null means no OE mark required |
| `pressure_normal_bar` | float | nullable | `2.3` | Partly laden |
| `pressure_laden_bar` | float | nullable | `2.7` | Fully laden; differs front/rear and is why pressures sit here, not on the tyre |
| `tpms_required` | bool | required | `true` | |
| `year_from` / `year_to` | year | nullable | `2019` / `null` | Fitments change mid-cycle |
| `markets` | string[] | nullable | `["SE","EU"]` | Approval is regional |

## 3. Relationships

| Relationship | Start | End | Intent | Properties |
|---|---|---|---|---|
| `HAS_FITMENT` | `VehicleVariant` | `WheelFitment` | Every approved wheel/tyre configuration for the variant | None |
| `FITS_TYRE` | `WheelFitment` | `TyreSpec` | The tyre size on one axle | `axle` |
| `FITS_RIM` | `WheelFitment` | `RimSpec` | The rim on one axle | `axle` |

`axle` is `enum(front, rear, all)`. A square fitment has **one** `FITS_TYRE`
with `axle = "all"`. A staggered fitment has **two**, `front` and `rear`. This
is what keeps the GLK's `235/45 R20` front paired to its `255/40 R20` rear
inside a single node, and it is the whole reason for reifying.

```
(:VehicleVariant)-[:HAS_FITMENT]->(:WheelFitment)-[:FITS_TYRE {axle:"front"}]->(:TyreSpec)
                                        │        -[:FITS_TYRE {axle:"rear"}] ->(:TyreSpec)
                                        │        -[:FITS_RIM  {axle:"front"}]->(:RimSpec)
                                        └────────-[:FITS_RIM  {axle:"rear"}] ->(:RimSpec)
```

## 4. Invariants

1. A `WheelFitment` has either one `FITS_TYRE {axle:"all"}` or exactly two,
   `front` and `rear` — never a mix, never three.
2. `is_staggered` is true **iff** two distinct `TyreSpec` nodes are attached.
3. For any `(FITS_TYRE, FITS_RIM)` pair on the same axle,
   `TyreSpec.rim_diameter_in = RimSpec.diameter_in`. A violation is a data
   fault, not a valid alternative.
4. `TyreSpec` and `RimSpec` are never variant-specific. Anything that varies by
   vehicle belongs on `WheelFitment`.
5. `homologation_mark` participates in fitment identity. Two fitments differing
   only by mark are two nodes, because `N0` and `N1` are not substitutable.

## 5. Deliberately out of scope

**Tyre products.** `Michelin Pilot Sport 4 235/45R18 94W XL MO` is a sellable
article, not a vehicle fact. It belongs in a catalogue with pricing and stock,
joined to `TyreSpec` by size and to `WheelFitment` by mark. Adding brand and
pattern here would turn a deduplicated spec node into an inventory table.

**Wheel articles.** Same argument for a specific alloy part number.

The join point is deliberate: a future `TyreProduct` attaches with
`(:TyreProduct)-[:HAS_SIZE]->(:TyreSpec)` and satisfies a `WheelFitment` when
its size matches and it carries the required mark.

## 6. What our sources can populate today

| Source | Populates | Gap |
|---|---|---|
| Transportstyrelsen | `TyreSpec` front and rear at 91.6% fill, per axle, 26,566 distinct sizes | No rim data beyond `wheelbase1`; no offset, PCD, or mark |
| TecDoc (current dump) | Nothing — the catalogue has no wheel or tyre entity at all | Everything |
| OEM data provider | Unknown — this is the open question worth testing | — |

TS alone fills `TyreSpec` and the axle pairing on `WheelFitment`, which is
already more than the graph holds today. `RimSpec` needs a source we do not
currently have, which is the specific thing to ask an OEM provider for: offset,
PCD, centre bore and the homologation mark. Those four fields are what turn a
size into a fitment.
