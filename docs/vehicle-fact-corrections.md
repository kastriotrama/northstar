# A person's corrections of a car's data

On the Vehicles tab, next to choosing a KType, a person can correct the data the
matcher reads for **one car**, and apply the same correction to **the other cars
like it** after a check of what it would change (see "One correction for many
cars" below):

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
| `production_month` ("Build month") | 1–12 (the lookup lists them in `values`) | with the year, as the build month (YYYYMM) |
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
matcher inputs. The evidence holds no plate, VIN or vehicle id. `group_id` names
the decision event that wrote the row (a foreign key to
`core.vehicle_correction_decisions`); it is empty for one car's own correction.

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
- A CHECK that reads a JSON key is NULL-safe (`coalesce(..., false)`): a row whose
  evidence lacks the key is refused instead of passing as NULL, and the verifier
  insists on that wrapping.

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
- When another source later states the value a correction holds, that source's
  word is kept behind the correction, so a withdrawal falls back to it instead of
  emptying the field.
- `reproject_corrections` puts the copy back after another writer changed it.
- **A stale copy is never matched on.** A vehicle value whose source is
  `correction:<id>` is handed to the matcher only when the field's head is a `set`
  with exactly that id (for the fuel: on any of its three columns). Otherwise it is
  skipped, the derivation underneath stands, and the car's lookup names the field
  in `copy_drift` (empty by default).

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
  "evidence_fingerprint": "lookup.evidence_fingerprint; required for set and ignore",
  "confirm_change": "optional, default false; see 409 confirmation_required"
}
```

Two things guard the car:

- **A resolved car never changes or loses its KType silently.** A `set` or an
  `ignore` on a car the matcher resolves today is first evaluated with the
  correction laid on top (the real matcher, nothing written). If the car would no
  longer resolve, or would resolve to another KType, the answer is 409
  `confirmation_required` with `detail: {code, message, before: {terminal, ktype},
  after: {terminal, ktype}}`; the same request with `confirm_change: true` is
  recorded. A withdrawal and the release of a stopped car are not asked about.
- **The car is checked again under its lock.** After locking the vehicle row the
  car is read again through the matcher's seam (no matcher run) and a hash of the
  matcher input is compared with the lookup's (`matcher_input_hash`); a car that
  changed in between is refused with 409 `evidence_changed`.

Answers the car's lookup as evaluated after the write: 201 when recorded, 200 for a
replay of the same operation. Errors carry `detail: {"code", "message"}`; only a 503
is worth retrying.

| Status | `code` | Meaning |
|---|---|---|
| 404 | `vehicle_not_found` | no such vehicle |
| 409 | `operation_id_reused` | the id already recorded something else |
| 409 | `correction_changed` | someone changed the field's correction since the screen loaded |
| 409 | `evidence_changed` | the car's matching is not what the screen showed, or changed before its lock was taken |
| 409 | `confirmation_required` | a resolved car would lose or change its KType; resend with `confirm_change: true` |
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

- `corrections`: the head of every chain the car has (a withdrawn one too); each
  with `decision: {decision_id, scope_label, member_count, reviewer} | null`, the
  decision about many cars that wrote it;
- `matcher_input_hash` and `copy_drift` (see above);
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

## One correction for many cars

After entering a correction for one car, a person chooses whom it applies to:
only this car, the cars with exactly the same data, or "all cars like this".
For the last two **nothing is saved before a check**: the matcher runs on each
car of the scope as it is and with the correction, and the result says what
would change, car by car. Rules that always hold:

- What was checked is what is written: a decision never reaches a car that was
  not evaluated in its check, and a car that changed since (its matcher input
  hash differs under its row lock) or that a person corrected meanwhile is left
  out and counted.
- A car the correction would harm -- a resolved car losing its KType (`lost`) or
  moving to another (`moved`), an unresolved one ending on a harder terminal
  (`worse`) -- is written only when the person includes it. A check with harmed
  cars, and at least as many of them as cars it fixes, cannot be applied at all.
- A car that already carries a person's correction of the field is left as it is.
  A KType a person chose is never changed; the existing choice logic flags it.
- A correction is evidence for the matcher, never a bypass: no matcher code
  changed. `TecDocDryRunEvaluator.evaluate(..., remember=False)` only keeps a
  check from growing the evaluator's memo; the evaluation is the same.
- The whole decision can be undone as one; one car of it by the one-car withdraw.

`normalization_stop` stays one car at a time.

### Scopes (`vehicle_corrections/scope.py`)

A scope is a list of plain conditions on `core.vehicles` columns (`equals`,
`is_empty`, `gte`, `lte`; never a computed key), compiled by
`ingestion.vehicle_core_query.compile_vehicle_filter`. `is_empty` is new for
vehicle filters: NULL, or for text the empty string. Every scope starts with the
anchor car's `manufacturer` and carries its `vehicle_scope`; a car without a
manufacturer offers no scope beyond itself (`scope_too_broad`).

- `same_data` pins every value the matcher reads off a vehicle: `model_family`,
  `production_year`, `production_month`, `power_kw`, `displacement_cc`,
  `engine_code`, `drive_type`, `bodywork_form`, `fuel`, `fuel_secondary`,
  `electrification_type`.
- `like_this` is a ladder per corrected field, narrowest first; each rung is its
  own option (`rung`, 0 the narrowest). Where the car has no value for a column a
  rung names, the rung pins its absence.

| Field | Rungs (each besides make and vehicle type) |
|---|---|
| `engine_code`, set | model + present code + power; then model + present code |
| `engine_code`, ignore | model + present code |
| `engine_code`, car has none | model + power + displacement + fuel |
| `drive_type` | model + registry type code + present drive (or none); then model + present drive |
| `bodywork_form` | model + registry type code + present body |
| `power_kw` | model + engine code (or none) + present power |
| `model_family` | registry brand text + registry model text + present model |
| `manufacturer` | registry make code + registry brand text |
| `fuel`, `electrification_type` | model + engine code (or none) + present value |
| `displacement_cc`, `production_year`, `production_month` | none: same data only |

A person may narrow any option on `engine_code`, `power_kw`, `displacement_cc`,
`production_year` (also from/to), `variant_code`, `version_code`,
`type_approval`, `drive_type`, `bodywork_form`, `fuel`. The conditions are always
rebuilt on the server from the car itself; the request only says which option is
meant. The SQL only selects candidates: each one is then read through the read
seam and must have the same present value **the matcher uses** as the anchor
(`not_like_this` otherwise).

### The check (`vehicle_corrections/preview.py`)

A job in the API process's memory (a restart loses it; check again). Each car is
sorted into exactly one outcome:

| Outcome | Meaning |
|---|---|
| `gained` | not resolved before, resolved after |
| `lost` | resolved before, not after |
| `moved` | resolved before and after, to another KType |
| `same` | resolved to the same KType before and after |
| `worse` | not resolved either time, and the terminal got harder (tie < no candidate < hard conflict) |
| `still_unresolved` | not resolved either time, no harder than before |
| `no_effect` | the matcher would be handed exactly the same |
| `already_corrected` | a person's correction stands on the field |
| `not_like_this` | failed the exact check |

`gained`, `lost` and `moved` are the impact report's own definitions
(`impact.resolution_change`). Also counted: cars with a person's KType choice
(`with_choice`), those the matcher would then resolve to another KType
(`choice_would_disagree`), and for `gained` cars the engine proxy
(`engine_check`) -- every gained car is `unchecked` there when the corrected
field is the engine code itself.

Safe on the small live server: one check at a time (a second is answered 429),
at most `CORRECTION_PREVIEW_MAX_CARS` cars (default 500, a seeded sample of a
larger scope) and `CORRECTION_PREVIEW_MAX_SECONDS` (default 240), stoppable
between two cars, read in pages of 25 under statement timeouts (5 s for the
scope, 15 s per page), each distinct matcher input evaluated once and nothing
added to the evaluator's memo. The last 10 checks are kept; one that ended more
than 30 minutes ago cannot be applied. `complete` is true only when every car of
the scope was checked; only then can it be applied.

### Decisions (`core.vehicle_correction_decisions`)

Append-only, one row per event of a decision (`propose`, `apply`, `withdraw`),
chained by position exactly like the corrections table (migration:
`ingestion/vehicle_correction_decision_migrations.py`, run by
`migrate-vehicle-core` before the corrections migration). The root carries what
was decided (field, action, value, scope, the sentence the person saw, copies of
the make and the model); every event carries the check it rests on
(`measurement`). The database refuses an application whose measurement is not
complete, a measurement without its numbers, a missing reason for anything but a
proposal, and any UPDATE, DELETE or TRUNCATE.

Each written car gets a normal correction row with `group_id` = the event and
`correction_id = uuid5(event_id, vehicle_id + ":" + field)`, so a retry writes
nothing twice. Applying and withdrawing are one transaction each: the vehicles'
rows are locked in id order (3 s lock timeout, 503 `vehicles_busy`, nothing
written), then the event, the member rows and the vehicle copies.

### API

| Call | Answer |
|---|---|
| `POST /v1/vehicles/{id}/corrections/scopes` `{field, action, value}` | 200 `{scopes: [{kind, label, count, too_broad, rung, conditions, narrowable}]}`; a count that took over 5 s is `null` with `too_broad: true` |
| `POST /v1/vehicles/{id}/corrections/preview` `{field, action, value, scope: {kind, rung?, conditions?, narrow: []}, evidence_fingerprint?}` | 202 the check |
| `GET /v1/vehicle-corrections/previews/{preview_id}` | the check: `status`, `affected`, `cap`, `checked`, `complete`, `stopped_by`, `counts`, `engine_check`, `would_write`, `can_apply`, `blocked_by`, `error` |
| `GET /v1/vehicle-corrections/previews/{preview_id}/cars?outcome=&limit=&offset=` | `{preview_id, outcome, total, offset, cars: [{vehicle_id, plate, outcome, before, after}]}` |
| `DELETE /v1/vehicle-corrections/previews/{preview_id}` | stops it; the check as it stands |
| `POST /v1/vehicle-corrections/decisions` `{operation_id, preview_id, event: apply\|propose, include_changed, reviewer, reason}` | 201 (200 replay) `{decision_id, status, written, skipped: {changed_since_check, corrected_meanwhile}, counts, scope_label, written_by_outcome}` |
| `POST /v1/vehicle-corrections/decisions/{decision_id}/withdraw` `{operation_id, reviewer, reason}` | 201 (200 replay) `{decision_id, status, withdrawn, left_changed, member_count, scope_label, skipped: {changed_by_person}}` |
| `GET /v1/vehicle-corrections/decisions?limit=`, `GET .../{decision_id}` | status, field, action, value, scope, label, who, why, when, member count, measurement, events |

Errors carry `detail: {code, message}`. Besides the one-car codes for the
correction itself (`invalid_vehicle_id`, `vehicle_not_found`,
`field_not_correctable`, `invalid_value`, `value_unchanged`, `nothing_to_ignore`):

| Status | `code` | Meaning |
|---|---|---|
| 429 | `busy` | another check is running |
| 409 | `evidence_changed` | the anchor car is not what the screen showed |
| 422 | `scope_too_broad` | no manufacturer, or the scope could not be read in 5 s |
| 422 | `scope_not_offered`, `invalid_scope` | the field has no such scope; a rung, column or comparison that cannot be used |
| 404 | `preview_not_found`, `decision_not_found` | |
| 409 | `preview_expired`, `preview_incomplete` | the check is too old; it did not cover every car, is running or failed |
| 409 | `operation_id_reused`, `decision_changed` | |
| 422 | `reason_required`, `nothing_to_apply`, `harms_more_than_it_fixes`, `nothing_to_withdraw`, `not_storable` | |
| 503 | `vehicles_busy`, `unavailable` | nothing was saved; retry with the same id |

A `propose` stores the decision with its (possibly sampled) measurement and
writes no car; it is what the panel offers when a check was not complete.

## How it reaches live

- Schema: `northstar-ingest migrate-vehicle-core` creates and verifies the
  decisions table and then the corrections table, right after the choices table. The production deploy script runs it before
  starting the new code. A local database needs it once; until then the lookup
  answers 503.
- `BUILD_VERSION` is the code version stored with each correction and decision.
- `CORRECTION_PREVIEW_MAX_CARS` and `CORRECTION_PREVIEW_MAX_SECONDS` bound a check
  (`.env.example`).
- Exports of these rows hold cars' data and are team-only.

## Not built yet

- **Live's corrections are not carried into a pilot rebuild.** The pilot builder
  creates both tables, copies the corrections of slice cars and every decision
  the *source* holds, and refuses a cut that leaves a corrected car outside the
  slice; nothing brings live's rows into the source (no export/import), so the
  runbook stops a switch while live holds any (`docs/PRODUCTION_DEPLOYMENT.md`).
- Measuring a proposal on all cars, applying a decision to cars that arrive
  later, and rulings of the kind "these two values mean the same".
- A check lives in one API process's memory: it is not shared between workers.
- Registry-text corrections; a Vehicles list filter for corrected cars.
- `check-`, `export-` and `import-` commands (`reproject_corrections` is the repair
  the check will call).
- Authentication: the reviewer is the name the person types.
