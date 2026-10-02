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
- A car has one chain. Each new row supersedes the car's current row; the row
  nothing supersedes is the **current choice**. A `withdraw` row on top means
  "no choice".
- The database enforces the chain: a row supersedes only a row of the same
  vehicle (composite foreign key), a row is superseded at most once and a vehicle
  has one root (partial unique indexes). UPDATE, DELETE and TRUNCATE are refused
  by triggers.
- The migration verifies definitions, not just names: columns, every constraint,
  the three indexes and both triggers (enabled).

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
state cannot undo a choice.

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
| 503 | `unavailable`, `vehicle_busy` | nothing was saved; retry with the same id |

`GET /v1/vehicles/{vehicle_id}/ktype-choices[?evidence=true]` lists the car's
choices from the current one backwards. It does not run the matcher.

`GET /v1/vehicles/matching/lookup` gained `evidence_fingerprint`, `choice`,
`effective_ktype` and `effective_source` (`person` or `matcher`). The matcher's own
`terminal`, `top_ktype` and candidates are unchanged.

### When a choice needs another look

Checked on every lookup (one extra indexed read, no second evaluation, nothing
written). `choice.needs_review` is true and `choice.stale_reasons` names why:

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
- `BUILD_VERSION` (set by `infra/production/deploy.sh` to the Git commit) is the
  code version stored with each choice.

### Not built yet

- **Pilot rebuilds do not carry choices yet.** The pilot builder does not know
  this table. A rebuild cut from a full build without live's choices would replace
  live's database and lose them. Before the next pilot rebuild the builder must
  learn the table (migration set, table class, pinning cars that have a choice,
  verification) and the export/import commands must exist.
- `check-ktype-choices`, `export-ktype-choices`, `import-ktype-choices`.
- The Vehicles list filter and KType column in the web app (the API side works:
  filter on `match_state` = `manual` / `manual_none`).
