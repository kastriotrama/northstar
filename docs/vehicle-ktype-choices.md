# A person's KType choice per car

On the Vehicles tab a person can choose one of a car's candidate KTypes, record
"none of these", or withdraw an earlier choice. The unit is one car. Works for
every verdict, including overriding an automatic match.

**PostgreSQL only.** A choice never writes Neo4j, aliases, canonical IDs,
`vehicle_variant_id`, the enrichment ledger, the review queue or the match
decision tables. Promotion (`docs/ktype-match-promotion-contract.md`) reads only
`resolved` decision heads, so a person's choice is never promoted to the graph.

## Backend

### Storage

`core.vehicle_ktype_choices` (migration: `ingestion/vehicle_ktype_choice_migrations.py`)
is append-only. Each row is one action (`choose`, `none`, `withdraw`) with who,
when, an optional reason, and the evidence shown: matcher inputs, the candidate
list with scores and fields, the catalog batch, the code version and the
automatic verdict at that time. The evidence holds no plate, VIN or vehicle id.
It does hold licensed TecDoc values, so any export of these rows is team-only.

- `choice_id` is the client's operation UUID. Resending the same request records
  once; the same id with different content is rejected.
- A car has one chain. Every row carries its place in it (`chain_position`, 0 for
  the first); each new row supersedes the car's current row and takes the next
  position. The row with the highest position is the **current choice**. A
  `withdraw` row on top means "no choice".
- The database enforces the chain declaratively: one row per vehicle and position
  (unique key), the first row supersedes nothing (CHECK), and every later row
  supersedes exactly the same vehicle's row one position below it (a foreign key
  that carries the position through a generated column). Positions only go down
  along the links, so even a single statement inserting several rows -- an import,
  a bulk copy -- cannot store a cycle, a detached chain, a second root, a fork or
  a link into another vehicle; a valid chain loads in any row order. UPDATE,
  DELETE and TRUNCATE (also via `TRUNCATE core.vehicles CASCADE`, MERGE and
  `ON CONFLICT DO UPDATE`) are refused by triggers.
- The migration verifies definitions, not just names: columns (with the generated
  column's expression), every constraint (validated, not deferrable), both
  triggers (enabled, no WHEN clause, no column list, no other trigger), the
  trigger function's body, no rewrite rule, and that the table is not UNLOGGED.
- Not enforced by the database: a withdrawal on top of a withdrawal. The service
  refuses it; a row written past the service would be harmless (the car still has
  no choice).

Why not the enrichment ledger: ledger ids are database-local, and choices made on
live must be copied verbatim into the full build and the next pilot cut. UUID-keyed
rows can be; ledger rows would have to be re-minted and would lose their dates.
The `review:` source encoding of vehicle-core is reused (below).

`core.vehicles.ktype`, `match_state` and their two `field_sources` keys are a
**copy** of the current choice, written in the same transaction, so the Vehicles
list can filter on them and the record shows the source:

| Current choice | `ktype` | `match_state` | `field_sources` |
|---|---|---|---|
| chosen | the KType | `manual` | `ktype` and `match_state` = `review:<choice_id>@<date>` |
| none of these | NULL | `manual_none` | `match_state` only |
| withdrawn, or none | NULL | NULL | both keys removed |

The table is the truth; the lookup reads the table. `save_vehicles` no longer
overwrites the matching columns of an existing vehicle, so a job holding an older
state cannot undo a choice. The copy's `field_sources` are rebuilt from the row
being updated, so a repair run (`project_choices` without ids, which takes no row
lock) keeps a source key another writer commits meanwhile. Without ids the repair
also clears a stray `ktype`, `match_state` or source key that no choice stands
behind; it reads `core.vehicles` once, so it is a maintenance step, not a request.

### API

`POST /v1/vehicles/{vehicle_id}/ktype-choices`

```json
{
  "operation_id": "uuid, minted once per action, resent unchanged on retry",
  "action": "choose | none | withdraw",
  "ktype": "required exactly when action is choose",
  "reviewer": "1-120 characters",
  "reason": "optional, up to 1000 characters",
  "supersedes_choice_id": "lookup.choice.choice_id as shown (a withdrawn one too), or null",
  "evidence_fingerprint": "lookup.evidence_fingerprint; required for choose and none"
}
```

Answers the car's refreshed lookup: 201 when recorded, 200 for a replay of the
same operation. Errors carry `detail: {"code", "message"}`:

| Status | `code` | Meaning |
|---|---|---|
| 404 | `vehicle_not_found` | no such vehicle |
| 409 | `operation_id_reused` | the id already recorded something else |
| 409 | `choice_changed` | someone changed the car's choice since the screen loaded |
| 409 | `evidence_changed` | the car's matching is not what the screen showed |
| 422 | `invalid_vehicle_id`, `ktype_not_a_candidate`, `nothing_to_withdraw` | |
| 422 | `not_storable` | the database refused the content itself (a value it cannot store, a constraint); the same request would be refused again |
| 422 | (validation list) | a malformed body: `detail` is FastAPI's list of `{loc, msg}`; control characters in `reviewer`, `reason` (line breaks and tabs allowed) or `ktype` are refused here |
| 503 | `unavailable`, `vehicle_busy` | nothing was saved; retry with the same id |

Only a 503 is worth retrying. The web offers "Try again" for 5xx and network
errors only.

`GET /v1/vehicles/{vehicle_id}/ktype-choices[?evidence=true]` lists the car's
choices from the current one backwards. It does not run the matcher.

`GET /v1/vehicles/matching/lookup` gained `evidence_fingerprint`, `choice`,
`effective_ktype`, `effective_source` (`person` or `matcher`) and
`rule_set_version`. The matcher's own `terminal`, `top_ktype` and candidates are
unchanged. The stored evidence records three versions: the code (`BUILD_VERSION`),
the confidence policy and the active translation rule set.

`POST /v1/vehicles/search` filters on `match_state` (`manual`, `manual_none`) and
each row carries `ktype` and `match_state`; the Vehicles list's "KType choice"
filter and KType column use them.

### When a choice needs another look

Checked on every lookup of a car that has a choice (the car's own read says
whether it has one, so a car nobody decided costs no extra connection; a decided
car costs one indexed read; no second evaluation, nothing written). `choice.needs_review` is true and `choice.stale_reasons` names why:

| Reason | Applies to | When |
|---|---|---|
| `catalog_batch_changed` | chosen, none | the catalog batch differs from the stored one |
| `evidence_changed` | chosen, none | a stored matcher input differs today (`changed_inputs` has then and now) |
| `ktype_not_a_candidate` | chosen | the KType is in the catalog but not among today's candidates |
| `ktype_not_in_catalog` | chosen | the KType is gone from the catalog |
| `new_candidates` | none | a KType is offered now that was not offered then |

Nothing is ever changed by the check. The choice stays the car's KType until a
person keeps it (choosing the same KType again), changes it or withdraws it.

### Operating it

- Schema: `northstar-ingest migrate-vehicle-core` creates and verifies the table.
  The production deploy script already runs it before starting the new code. A
  local database needs it once; until then the lookup answers 503.
- `BUILD_VERSION` is the code version stored with each choice.
  `infra/production/deploy.sh` passes the Git commit as a build argument and the
  API image carries it, so a container started later by a plain
  `docker compose up` still reports it. An image built without the argument says
  `unknown`.
- A database where an earlier draft of this migration ran (none is known: the
  table never reached live) fails the verification; drop the empty table there
  and run the migration again.

### Not built yet

- **Pilot rebuilds do not carry live's choices yet. This must land before the
  next pilot rebuild.** What exists: the pilot builder creates the table (so
  lookups work on a pilot), copies the choices of slice cars the source holds,
  and refuses a cut that would leave a decided car outside the slice. What is
  missing: `export-ktype-choices` on live and `import-ktype-choices` into the
  full build (verbatim rows, re-projected copy), pinning decided cars into the
  slice, and a manifest check that every exported choice arrived. Until then the
  runbook stops a switch when live holds any choice
  (`docs/PRODUCTION_DEPLOYMENT.md`, step 6).
- `check-ktype-choices`: a command that runs the repair (`project_choices`
  without ids) and reports drift.
- Listing the choices that need another look. The flag is computed when a car is
  looked up; a list needs a batch evaluation, which the live server cannot do per
  request.
