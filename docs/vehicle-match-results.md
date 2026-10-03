# Stored match results (`core.vehicle_match_results`)

The matcher's outcome per car, stored, so that matching statistics and car lists
are read from a table instead of running the matcher. The matcher runs only to
fill or refresh this table, and live for the one car a person opens.

## What is stored

One row per NorthStar vehicle (`vehicle_id`, foreign key to `core.vehicles`):

| Column | Meaning |
|---|---|
| `state` | `resolved`, `several`, `one_unconfirmed`, `none`, `not_matchable` |
| `terminal` | the matcher's own outcome (`resolved`, `provisional`, `review_required`, `hard_conflict`, ...) |
| `ktype` | the accepted KType; filled only when `state = 'resolved'` (a CHECK enforces it) |
| `best_candidate_ktype` | the best candidate, also for unresolved cars; empty when there is no candidate |
| `confidence` | the matcher's confidence in the best candidate |
| `candidate_count`, `candidate_ktypes`, `candidate_confidences` | the KTypes that conflict with the car on nothing, best first (the matcher returns at most 5) |
| `separating_fields`, `missing_fields` | several: fields that differ between the possible KTypes, and of those the ones the car has no value for |
| `conflicting_fields` | none: fields the car conflicts with its best candidate on |
| `reason_codes` | the matcher's reason codes |
| `catalog_batch`, `matcher_version`, `run_id` | what produced the row |
| `input_hash` | sha256 of exactly what the matcher was handed for the car |
| `evaluated_at` | when the car was last read for matching |

States:

- `resolved`: the matcher accepted one KType. Other KTypes may have been
  compatible too; they are in `candidate_ktypes`.
- `several`: two or more compatible KTypes, none accepted.
- `one_unconfirmed`: exactly one compatible KType, not accepted (mostly provisional).
- `none`: every candidate conflicts, or there is no candidate.
- `not_matchable`: the car was stopped before matching.

The table is a cache: rows are overwritten when a car is matched again. A
person's KType choice is not stored here; it stays in `core.vehicle_ktype_choices`
and on `core.vehicles` (`match_state`, `ktype`). The overview counts a decided car
as `chosen` or `chosen_none`, whatever the matcher says.

`core.vehicle_match_runs` has one row per fill or refresh (mode, pins, counts,
status).

## Reading

Both endpoints take the Vehicles filter (`conditions`, `text`) and never run the
matcher.

- `POST /v1/vehicles/match-results/overview`: total cars, cars per state
  (including `chosen`, `chosen_none` and `not_evaluated`), terminals, the number
  of possible KTypes of tied cars, separating / missing / conflicting field
  counts, why cars are not matchable, how many stored rows may be out of date,
  and the latest run.
- `POST /v1/vehicles/match-results/cars`: the cars behind one state, paged by
  NOR ID (`after`, `limit`), each with its accepted or possible KTypes and the
  stored reasons. Optional narrowing: `missing_field`, `separating_field`,
  `conflicting_field`, `reason`, `ktype`, `candidate_count`.

For one car in full (every candidate's values, the decision trace) use
`GET /v1/vehicles/matching/lookup?vehicle_id=...`, which evaluates that car live.

## Filling and refreshing

From `apps/backend`. The catalog batch is required.

```bash
# new and changed cars (the normal run; repeatable, continues a stopped run)
python -m scripts.refresh_vehicle_match_results --catalog-batch <batch> --workers 4

# a quick overview: a seeded random 10,000 cars
python -m scripts.refresh_vehicle_match_results --catalog-batch <batch> --sample 10000 --workers 4

# after a matcher or catalog change: every car again
python -m scripts.refresh_vehicle_match_results --catalog-batch <batch> --rebuild --workers 4
```

A car is selected for the normal run when it has no row, when its row is from
another catalog batch, or when the vehicle, its origin record's normalization or
a person's correction is newer than the row. A selected car whose `input_hash`
is unchanged is not matched again; only its `evaluated_at` moves.

A matcher change does not make rows stale by itself: unchanged cars keep their
rows until a `--rebuild`. The overview lists the matcher versions present.

Each page of cars is committed on its own. The tables are created by
`migrate-vehicle-core` (every deploy) and are carried by the pilot builder.

## On screen

Vehicles, Matching tab (`ns-match-results`): the overview of the current filter
as tiles and breakdowns; every number opens the cars behind it
(`ns-match-result-cars-dialog`), and the picked car is matched live beside the
list. Percentages are of the cars that have a stored result. The earlier "run
the matcher on a sample" view sits below it, folded, for checking a matcher
change before a refresh.

## Not done yet

- Refreshing a car's row at the moment a correction or a choice is saved; until
  then the normal run picks such cars up.
- Match state as a filter in the main Vehicles list.
