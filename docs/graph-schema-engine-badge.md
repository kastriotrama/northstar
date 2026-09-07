# Graph Schema Design — Engine Badge

| Field | Value |
|---|---|
| Version | `0.1` |
| Status | Proposed — extends `graph-schema-design.md` v0.4 |
| Scope | `USES_ENGINE.badge`; the normalized `engine_badge` field that feeds it |
| Depends on | §3.4 Engine, §3.7 VehicleVariant, §5.2 relationship catalog |

## 1. The gap

The core schema already names this fact and stores it nowhere. §3.7 says a
`VehicleVariant` deliberately has no name of its own:

> its display name is assembled by traversal (Manufacturer + ModelFamily +
> **engine badge** + body). Storing an assembled name would denormalize facts
> owned by neighboring nodes.

`badge` appears exactly once in the repository — in that sentence. So the
assembly rule references a component that no node or edge holds, and
`Mercedes-Benz` + `CLK` + *nothing* + `Coupé` cannot distinguish a CLK 200
from a CLK 320.

## 2. Where it belongs

On the `USES_ENGINE` edge, by the rule §3.4 already sets for Engine:

> **Intrinsic facts only** — power, torque, and emission standard vary per
> installation and live on the `USES_ENGINE` edge.

A badge is exactly that kind of fact. The same OM651 is badged `C 200 CDI` in
one car and `E 220 CDI` in another, so the badge belongs to the pairing, not to
the engine design and not to the variant.

| Property | Type | Required | Example | Meaning |
|---|---|---|---|---|
| `badge` | string | nullable | `"220 D"` | The marque's designation for this engine installation, without the family |

The family is excluded so assembly stays `ModelFamily + badge` with nothing
duplicated: `CLK` + `200 KOMPRESSOR`, `E-Class` + `220 D`.

BMW is the exception and is stored verbatim anyway: family `3 Series`, badge
`320D`, because `320D` cannot be split — `20D` is not a designation. Assembly
yields `BMW 3 Series 320d`, which is how parts catalogues write it.

## 3. What the badge is not

**Not the drivetrain.** `E 220 D 4MATIC` badges `220 D`; `4MATIC` is
`VehicleVariant.drive_type`, which the registry `is_4wd` flag already resolves
for 222,006 of the 222,574 rows whose model text names a 4WD system.

**Not the body.** `A4 Avant` has no badge at all. `Avant` is `BodyType`, and
the registry body code already resolves it — of 180,714 rows whose model text
carries a body word (`CROSS COUNTRY`, `SPORTBACK`, `SPORTS TOURER`), 100%
already resolve `bodywork_form` from the code, with `bodywork_source =
"registry"`.

**Not displacement.** `C 220 d` is a two-litre. The number tracks a positioning
tier, and reading it as capacity has been wrong since roughly 1993.

**Not trim.** `Pro`, `GTX`, `Avantgarde` are `VehicleVariant.trim_level`.

## 4. Where it comes from

The badge straddles two places in registry text. `E 220 D` matches the family
rule on the term `E 220`, so the designation number sits inside the matched
term while its qualifier is left trailing:

```
badge = (matched term − family designator) + engine qualifiers that follow
        "E 220"       − "E"                + "D"                = "220 D"
```

`rule_matches[].source_term` already records the matched term, so the
expensive half is free. A qualifier joins the badge when it is a known engine
word (`D`, `CDI`, `KOMPRESSOR`, `TFSI`) or carries a number (`T8`, `B5`, `55`);
a purely alphabetic word such as `AVANT` or `CROSS` ends it.

Two marques need shape-specific handling. BMW glues drivetrain to badge in
`X5 xDrive30d`, which yields `30D` with the drivetrain dropped as a fact
another field owns. Volvo and Audi write code badges — `T8 Twin Engine`,
`55 TFSI e` — which the number test admits.

The normalization pipeline emits this as `engine_badge`, which raised
`PIPELINE_VERSION` to `normalization-pipeline-v7`.

## 5. Completeness

Structure degrades; source text does not. The full registry string is retained
independently as an `Alias` with `alias_type: "model_name"`, already in the
enum, carrying its own confidence and provenance.

That matters because `USES_ENGINE.badge` needs an edge to live on, and a large
share of registry rows never resolve an `Engine`. When they do not, the badge
has no home on the graph — but the Alias still holds `CLK 200 KOMPRESSOR`
verbatim, so nothing is lost and a later engine resolution can populate the
edge without re-reading the source.
