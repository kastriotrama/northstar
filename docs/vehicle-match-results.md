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

The endpoints take the Vehicles filter (`conditions`, `text`) and never run the
matcher.

- `POST /v1/vehicles/match-results/counts`: cars per state (including `chosen`,
  `chosen_none` and `not_evaluated`) and how many stored rows may be out of
  date. One grouped query (0.3 s for 500k cars).
- `POST /v1/vehicles/match-results/overview`: the counts plus terminals, the
  number of possible KTypes of tied cars, separating / missing / conflicting
  field counts, why cars are not matchable, and the latest run. One pass over
  the filtered cars (about 2 s for all 500k, 0.4 s for one make).
- `POST /v1/vehicles/match-results/cars`: the cars behind one state, paged by
  NOR ID. The web no longer uses it: the Vehicles list does this job.

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

Everything is in Vehicles, Cars: there is no Matching tab.

- Above the list, `ns-match-results` shows the cars of the filter per state.
  A state is a button that narrows the list to its cars. The counts leave out
  the list's own matching clauses, so picking a state never hides the others.
- "What stands between the open cars and one KType" opens the breakdown (read
  only then). Each of its counts narrows the list to exactly those cars.
  Its section "Matched — to check" counts the matched cars whose KType is
  candidate-only and was accepted as the only KType they fit
  (`docs/vehicle-match-decisions.md`).
- The KType column shows a person's choice, else the accepted KType, else the
  first two possible KTypes with "+N" (all of them, with confidence, in the
  tooltip), else the state.
- Filters: "Matching result" and "KType" (cars the KType is accepted, chosen or
  possible for).
- Clicking a row opens the car with its candidate KTypes, matched live.

The on-screen "run the matcher on a sample" tool is gone; its API
(`/v1/vehicles/matching/summary`) is still there. Use
`scripts/match_impact_report.py` to measure a matcher change.

## Kept current when a person saves

A saved correction or KType choice refreshes that car's row before the answer
goes back (`MatchResultSync`, called by the choice and correction services
after their own transaction). A decision about many cars refreshes the cars
its corrections name: up to five at once, more on a background thread. A
refresh that fails is logged and never fails the save; the normal run picks
the car up, because the vehicle is then newer than its row. Each such save
writes a run of mode `vehicles`; the overview's "last run" leaves those out.

## In the Vehicles list

These are filter fields of the Vehicles list, its count and its facets. None is
a column of `core.vehicles`: each is compiled into probes of the stored results
by the vehicle's key.

| Field | Meaning |
|---|---|
| `match_result` | state (`equals` / `not_equals`): the five matcher states, `chosen`, `chosen_none`, `not_evaluated` |
| `match_missing_field` | tied cars lacking this separating field |
| `match_separating_field` | tied cars whose possible KTypes differ on this field |
| `match_conflicting_field` | cars conflicting with their best candidate on this field |
| `match_reason` | cars carrying this reason code |
| `match_candidate_count` | cars with exactly this many possible KTypes |
| `match_ktype` | cars this KType is accepted, chosen or possible for |

Each list row carries `match_result`, `automatic_ktype`, `candidate_ktypes` and
`candidate_confidences`.

## Kept current when a reviewer rule changes cars

A reviewer rule from the TS data screen changes the cars it covers, not their
stored match results. When a rule finishes running, or is retired, the API
matches every changed car again in the background
(`MatchResultSync.population_changed`): one such refresh at a time, and a
change that arrives while one runs makes it run once more. The same refresh
can be asked for by hand: `POST /v1/vehicles/match-results/refresh`, the
"Match them again now" button beside the "cars changed after they were
matched" line. The counts answer `refreshing` while it works.

`GET /v1/vehicles/match-results/reviewer-rules` lists the latest reviewer
rules as changes to many cars (who, when, conditions, what it sets, vehicles
reached, vehicles whose match result is older than the change). Vehicles >
Decisions shows it under the corrections applied to several cars.

## Not done yet

- Overview query time has only been measured with a partly filled table.
- Rows are not refreshed when learned rules are applied from the command line
  or records are re-normalized; run the normal refresh after those, or press
  "Match them again now".
