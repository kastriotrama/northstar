# A person's corrections of one car's data

On the Vehicles tab, next to choosing a KType, a person can correct the data the
matcher reads for **one car**:

- **set** a value for a field (fill a missing one, or replace a wrong one);
- **ignore** the car's present value ("this is wrong, the right value is not
  known"): the matcher then has no value for the field on this car;
- **withdraw** a correction: the car's own data applies again.

The car is matched again at once and the answer is the new lookup. A correction is
evidence for the matcher, never a bypass: every guard stays on. If the car still
ties, the person picks the KType (`docs/vehicle-ktype-choices.md`).

**PostgreSQL only.** A correction never writes Neo4j, aliases, canonical IDs, the
enrichment ledger, the review queue or the match decision tables.

## What can be corrected

Defined once, in `api/app/features/vehicle_corrections/fields.py` (`SPECS`): the
name, the label, what a value looks like, the matcher's evidence keys, the keys the
matcher reads it from and the vehicle columns that carry the copy.

| Field | Value | Matcher reads |
|---|---|---|
| `manufacturer`, `model_family`, `engine_code` | text, 1–80 characters | the field |
| `power_kw`, `displacement_cc` | whole number, 1–2000 / 1–20000 | the field |
| `drive_type`, `bodywork_form` | one of the reviewed values (the TS screen's vocabulary) | the field |
| `production_year` | 1880 to next year | the field |
| `production_month` ("Build month") | 1–12 | with the year, as the build month (YYYYMM) |
| `fuel` | 1 to 3 energy carriers, comma-joined in vocabulary order (`petrol,electricity`) | `energy_sources` and `fuel_match_tokens` |
| `electrification_type` | `battery_electric`, `fuel_cell_hybrid`, `hybrid`, `plug_in_hybrid` | the field |

- The fuel's comparison tokens (the carriers plus `hybrid_petrol` / `hybrid_diesel`)
  come from normalization's own rule, `ingestion.normalization_rules.fuel_match_tokens`.
- A corrected year or month silences the normalization's own date
  (`production_date`): the build month is then the year and the month the matcher is
  handed, and an ignored one leaves no build month.
- Registry text (brand, model, variant) is not correctable. The matcher still reads a
  model from it, and prefers a model text the catalog recognizes over `model_family`.

### Releasing a stopped car

A car whose record's normalization asks for review (`tyre_size_unrecognized`, ...)
never reaches the matcher. On the pseudo-field `normalization_stop` a person can
`ignore` the stop (a reason is required) and `withdraw` the release. It is not a
value: `set` is refused, and it is not among `correctable_fields`. A released car is
handed to the matcher as a resolved record; the record itself keeps its status and
reasons, and `core.vehicles` is not written. A policy route (a motorhome) and a
failed normalization are other outcomes and cannot be released.

## Storage

`core.vehicle_fact_corrections` (migration:
`ingestion/vehicle_fact_correction_migrations.py`) is append-only. Each row is one
action on one field of one car, with who, when, an optional reason, the value the
matcher used before (`previous_value`, `previous_source`) and the evaluation the
person saw: the catalog batch, the code version, the automatic verdict and the
matcher inputs. The evidence holds no plate, VIN or vehicle id. `group_id` is
reserved for a decision that covers many cars and is always empty today.

- `correction_id` is the client's operation UUID. Resending the same request records
  once; the same id with different content is rejected.
- A field of a car has one chain. Each row carries its place in it
  (`chain_position`, 0 first) and supersedes the row one place below; the row with
  the highest position is the **current correction**. A `withdraw` on top means "no
  correction".
- The database enforces the chain with keys, one foreign key that carries the
  position, and CHECKs. Not even a statement inserting several rows can store a
  cycle, a detached chain, a second root, or a link into another car or another
  field. UPDATE, DELETE and TRUNCATE are refused by triggers.
- The migration verifies definitions, not just names: columns (the generated one
  too), every constraint, the group index, both triggers and their function, and that
  the table is durable and has no rewrite rule.

**The table is the truth for matching.** `vehicle_car_records` reads each car's
current corrections with the car (same connection, by the vehicle key) and lays them
over the vehicle's values last. The lookup, a summary job and
`scripts/match_impact_report.py` all go through it. A database without the table
fails that read (the lookup answers 503): a car is never matched as if nobody had
corrected it.

`core.vehicles` carries a **copy** of every `set`, for the list, the search and the
record view, written in the same transaction through the merge code:

- Its source is `correction:<correction_id>`. `correction` ranks above `review`, so
  a reviewer rule's value the correction displaces is kept behind it, a rule applied
  later waits behind it too, and a withdrawal brings back the rule's value when
  there is one, else the provider's.
- An `ignore` does not change the vehicle: the value stays visible, the matcher just
  is not handed it.
- The fuel is copied to `fuel` (first carrier), `fuel_secondary` (second, emptied
  when there is none) and `fuel_match_tokens`. An emptied second fuel carries no
  source, so a later import can fill it again on the copy; matching is not affected.
- A value the car already had is confirmed, not re-sourced (the merge's own rule),
  so a later import or rule can still change that column on the copy.
- `reproject_corrections` puts the copy back after another writer changed it.

## API

`POST /v1/vehicles/{vehicle_id}/corrections`

```json
{
  "operation_id": "uuid, minted once per action, resent unchanged on retry",
  "field": "one of correctable_fields, or normalization_stop",
  "action": "set | ignore | withdraw",
  "value": "text; given exactly when action is set",
  "reviewer": "1-120 characters",
  "reason": "optional, up to 1000 characters; required to release a stopped car",
  "supersedes_correction_id": "the field's corrections[].correction_id as shown (a withdrawn one too), or null",
  "evidence_fingerprint": "lookup.evidence_fingerprint; required for set and ignore"
}
```

Answers the car's lookup as evaluated after the write: 201 when recorded, 200 for a
replay of the same operation. Errors carry `detail: {"code", "message"}`; only a 503
is worth retrying.

| Status | `code` | Meaning |
|---|---|---|
| 404 | `vehicle_not_found` | no such vehicle |
| 409 | `operation_id_reused` | the id already recorded something else |
| 409 | `correction_changed` | someone changed the field's correction since the screen loaded |
| 409 | `evidence_changed` | the car's matching is not what the screen showed |
| 422 | `invalid_vehicle_id`, `field_not_correctable`, `invalid_value` | |
| 422 | `value_unchanged` | `set` to what the matcher already uses |
| 422 | `nothing_to_ignore` | no value to ignore, or the car is not stopped |
| 422 | `nothing_to_withdraw` | no correction in force on the field |
| 422 | `reason_required` | a release without a reason |
| 422 | `not_storable` | the database refused the content; the same request would fail again |
| 503 | `unavailable`, `vehicle_busy` | nothing was saved; retry with the same id |

`GET /v1/vehicles/{vehicle_id}/corrections` lists the car's corrections by field,
each from the current one backwards. It does not run the matcher.

`GET /v1/vehicles/matching/lookup` gained, for a vehicle:

- `corrections`: the head of every chain the car has (a withdrawn one too);
- `correctable_fields`: every field above with its label, type (`text`, `integer`,
  `list`), vocabulary, evidence keys, the value the matcher uses today and its
  source (`registry`, `ais`, `review`, `rule`, `derived`, `correction`), and the
  values the listed candidates carry for it;
- `stop_reasons`: why the car's record is stopped before matching, also while a
  release is in force.

`overlaid_fields[field]` is `correction` for a corrected or ignored field. The
evidence fingerprint covers the matcher inputs, so a correction changes it, and a
KType choice made before a correction is flagged `evidence_changed` until a person
confirms or changes it.

## How it reaches live

- Schema: `northstar-ingest migrate-vehicle-core` creates and verifies the table,
  right after the choices table. The production deploy script runs it before
  starting the new code. A local database needs it once; until then the lookup
  answers 503.
- `BUILD_VERSION` is the code version stored with each correction.
- Exports of these rows hold cars' data and are team-only.

## Not built yet

- **Pilot rebuilds do not carry corrections.** Like the choices table, the pilot
  builder does not know this one; a rebuild would lose live's corrections.
- A decision for many cars at once ("all cars like this") and its preview.
- Registry-text corrections; a Vehicles list filter for corrected cars.
- `check-`, `export-` and `import-` commands (`reproject_corrections` is the repair
  the check will call).
- Authentication: the reviewer is the name the person types.
