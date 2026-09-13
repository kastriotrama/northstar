# TecDoc canonical rules need a sealed table (deferred design)

Status: **sketched, not built.** Held as a plan by explicit request. Nothing in
this document has been implemented; it exists so the reasoning and schema
don't have to be re-derived from scratch next time this comes up.

## The core problem to solve

```
TS side:      Python dict  →  sealed DB table  →  matcher reads DB (falls back to Python)
                                     ↑ built earlier this session

TecDoc side:  Python dict  →  matcher reads Python directly
                    ↕
              tecdoc_resolution_rules (live, reviewable) → dashboard only, matcher never sees it
```

Two disconnected mechanisms on the TecDoc side, and the one that's
live/reviewable is the one the matcher ignores.

## How the TS side already solved this

Earlier this session, TS-side rule content moved from a bare Python catalog
(`ingestion/translation_dictionaries.py`) into a sealed database table:

- `core.translation_rule_definitions` + `core.translation_rule_definition_versions`
- Immutable once sealed: no `UPDATE`/`DELETE` on a definition row, no `INSERT`
  into a version already sealed (enforced by triggers, not convention)
- `import_rule_set()` writes a whole rule set under a version and seals it
- `load_rule_set_from_database()` reads it back
- `ingestion/active_rules.load_active_rules()` prefers the DB, falls back to
  the Python catalog when a version has no stored rows

This closed a real drift bug: content behind `ts-translation-v7` had grown
231 → 1,212 → 1,261 rules across three commits while every activated row
kept pointing at the same unchanged name.

## Why the TecDoc side doesn't have the same guarantee

`ingestion/tecdoc/reference_data.py` holds `_BODYWORK_CANONICAL_BY_KT086` and
its siblings for drive type and transmission type as bare Python dicts. Both
`canonical_promotion.py` (the promotion job) and
`ingestion/tecdoc/match_run_adapters.py` (the real TS↔TecDoc matcher) call
these functions directly — confirmed in code, not assumed:

```python
# ingestion/tecdoc/match_run_adapters.py:232
bodywork_by_code = canonical_bodywork_by_kt086()
```

Separately, `core.tecdoc_resolution_rules` lets a reviewer rule on a raw
TecDoc code live, via `POST /gaps/resolve` on the TecDoc review dashboard —
visible immediately, fully reversible. But per `api/app/features/tecdoc_review/repository.py`'s
own comment:

> The two trailing joins are the Tier 1 promotion-loop closer... This does
> not reach the matcher (`tecdoc/match_run_adapters.py`), which reads the
> same table through its own path — see Tier 2.

So a reviewer's ruling can look "fixed" on the dashboard while doing nothing
for real matching. Same drift risk `ts-translation-v7` had, just not yet
caught here in the wild.

## How this surfaced

While fixing the `bodywork_form` gap (27,362 → 6 across three independent
root causes — candidate-only rows never computing `bodywork_link_status` at
all, ~9,800 motorcycles leaking into the passenger-car cohort because
TecDoc's own `is_pc` flag has no motorcycle category, and 4 missing
canonical codes), the four new bodywork codes had to be hardcoded directly
into `_BODYWORK_CANONICAL_BY_KT086` — the only mechanism that actually
reaches the matcher today. That's inconsistent with the 5 `transmission_type`
labels ruled the same session through `/gaps/resolve`, which only ever
reached the dashboard. That inconsistency is what surfaced this gap.

## The design

### 1. A new sealed table, mirroring the TS pattern

```sql
core.tecdoc_canonical_rule_definitions (
    rule_version      TEXT,
    canonical_field   TEXT,   -- 'bodywork_form' | 'drive_type' | 'transmission_type' | 'energy_sources'
    key_table         TEXT,   -- '086' | '082' | '085' | '088'
    source_code       TEXT,   -- '033', '051', ...
    canonical_value   TEXT,
    PRIMARY KEY (rule_version, canonical_field, source_code)
)

core.tecdoc_canonical_rule_definition_versions (
    rule_version, source_note, imported_by, rule_count, sealed BOOLEAN
)
```

One table covers all four TecDoc vocabularies via `canonical_field`, not four
parallel tables. Same immutability triggers as
`core.translation_rule_definitions`: no `UPDATE`/`DELETE` on a definition
row, no `INSERT` into a version once sealed.

### 2. The four `canonical_*_by_kt0xx()` accessors become DB-first, Python-fallback

Exactly the `load_active_rules` pattern:

```python
def canonical_bodywork_by_kt086(connection=None):
    if connection is not None:
        stored = load_canonical_rules_from_database(connection, "bodywork_form", active_version)
        if stored is not None:
            return stored
    return dict(_BODYWORK_CANONICAL_BY_KT086)   # bootstrap/fallback, unchanged
```

### 3. Both promotion and matching read the same accessor

`canonical_promotion.py` and `match_run_adapters.py` both call this one
function. That's the actual fix to the Tier 1 / Tier 2 split — there's no
longer a separate "matcher's own path"; a sealed version reaches promotion
*and* matching through one read, the same way TS rules already do.

### 4. `tecdoc_resolution_rules` keeps its current job: draft, not authority

A reviewer still rules on a raw code through `/gaps/resolve`, still sees it
live on the dashboard instantly, still fully reversible. Nothing about the
existing review workflow changes.

### 5. One new, deliberate "promote" step

Not automatic. Takes the accumulated `tecdoc_resolution_rules` rows and seals
them into a new `tecdoc_canonical_rule_definitions` version:

```python
def promote_reviewed_bodywork_rules(connection, *, rule_version, imported_by, source_note):
    reviewed = fetch_accepted_resolution_rules(connection, canonical_field="bodywork_form")
    import_canonical_rules(connection, reviewed, rule_version=rule_version, ...)
```

This is precisely what `api/app/features/tecdoc_review/service.py`'s own
docstring already names and defers:

> promoting a ruling made here into that file is a deliberate follow-up, not
> something this endpoint does itself.

This design is that follow-up.

## What this buys

- A reviewer's ruling reaches the dashboard the instant they click Resolve —
  unchanged.
- It reaches the **matcher** only when someone deliberately promotes it — no
  silent drift, no ambiguity about whether a fix touched real matches or
  just the dashboard.
- Every sealed version is immutable and auditable — the same guarantee that
  stopped `ts-translation-v7` from silently growing under one name, now on
  the TecDoc side too.
- One promotion/versioning mechanism for both TS and TecDoc rules, not two
  different shapes to reason about.

## Open question, not resolved

Should `energy_sources`/fuel folding live in this same table, or stay
separate? `core.vocabulary_alignments` already handles fuel with an
`equivalent`/`compatible` distinction, and a `compatible` row can name *more
than one* target for one source term (TS's `2wd` compatible with both
TecDoc `fwd` and `rwd`). This design is single-target per source code.
They're conceptually close but structurally different — worth deciding
before writing the migration, not after.

## Scope touched if built

- New migration module (mirrors `ingestion/rule_definition_migrations.py`)
- New rule-definitions module (mirrors `ingestion/rule_definitions.py`)
- `ingestion/tecdoc/reference_data.py` — four accessors gain DB-first reads
- `ingestion/tecdoc/canonical_promotion.py` — pass a connection through
- `ingestion/tecdoc/match_run_adapters.py` — pass a connection through
- `api/app/features/tecdoc_review/service.py` — the new "promote" action
