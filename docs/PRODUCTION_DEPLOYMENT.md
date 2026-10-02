# NorthStar production deployment

NorthStar uses the same pull/build/restart model as VD-AI without sharing its
containers, database, ports, or working tree. GitHub Actions connects to the
Hetzner host over SSH, fast-forwards `/opt/northstar`, validates Compose, builds
the API and ingestion images, and recreates only the `northstar` Compose project.

## Server layout

- application checkout: `/opt/northstar`;
- public gateway: `http://128.140.71.62:8765`;
- application screen: `/normalization-review`;
- PostgreSQL, Neo4j, Elasticsearch, and Redis: private Compose network only;
- persistent data: named Docker volumes prefixed by the Compose project;
- secrets: `/opt/northstar/.env.production` and
  `/opt/northstar/infra/production/htpasswd`, never Git.
- restricted showcase data: `/opt/northstar-private/ts-ktype-resolved-showcase-1000.json`,
  mounted read-only into the API and never stored in Git.

VD-AI continues to own port 80 and the host `vehicle_db`. NorthStar does not
connect to, migrate, or modify that database.

## First deployment

1. Clone the repository into `/opt/northstar`.
2. Copy `infra/production/.env.production.example` to `.env.production` and
   replace every placeholder with a random secret.
3. Create `infra/production/htpasswd` for the web administrator.
   The deploy script sets this password-hash file to mode `0644` so the
   unprivileged nginx worker can read the bind mount. The clear-text password
   must not be stored in the repository or this file.
4. Run `./infra/production/deploy.sh`.
   The script fails closed unless the restricted showcase file exists and is
   readable at `NORTHSTAR_PRIVATE_DATA_DIRECTORY` (default
   `/opt/northstar-private`).
5. Import a reviewed portable bundle with the tools profile, for example:

   ```sh
   docker compose --env-file .env.production -f docker-compose.production.yml \
     --profile tools run --rm ingestion import-normalization-bundle \
     --file /bundles/019fadda-d238-75d3-8312-142dfdce2612/northstar_ts_normalization_atlas_5000_2026-08-06.xlsx
   ```

6. Verify the authenticated web screen and `/health` response.

## GitHub Actions

The production environment requires these repository secrets:

- `NORTHSTAR_HETZNER_IP`;
- `NORTHSTAR_HETZNER_SSH_KEY`.

Deployment runs after a push to `develop` or by manual workflow dispatch. The
workflow does not create secrets, import datasets, or delete persistent volumes.

## Pilot database (slice of the full build)

The full local build (about 63 GB) does not fit on live. The pilot database is a
slice of it that works like the full build: a seeded random pick of registered
passenger cars (500,000 by default) with every row those cars need, every rule,
and the one TecDoc catalog batch live is pinned to. It is built locally, next to
the full build, and shipped as a dump. The dump holds licensed TecDoc data and
real plates and VINs: team-only, never Git.

### Build

From `apps/backend`, with `DATABASE_URL` pointing at the full build:

```sh
# Plan only: prints the tables, row counts and estimated size. Creates nothing.
python -m scripts.build_pilot_database --target-database northstar_pilot \
  --catalog-batch tecdoc-0326-canonical-full-prod-v4-20261001

# Build and verify. A verified build writes northstar_pilot.manifest.json.
python -m scripts.build_pilot_database --target-database northstar_pilot \
  --catalog-batch tecdoc-0326-canonical-full-prod-v4-20261001 --commit \
  --manifest northstar_pilot.manifest.json
```

Rehearse first with a small slice to a scratch target (`--size 2000
--target-database northstar_pilot_rehearsal`), dump it, restore it into another
scratch database and run `--verify` there; then drop both.

- The full build is only read (read-only connection, one snapshot). The target
  is a new database on the same server; the script refuses a target that exists
  or that is the source. `--replace` drops an earlier pilot build first, and
  only a database this script created.
- Options: `--size` (500000), `--seed` (`northstar-match-impact-v1`), `--scope`
  (`passenger`), `--no-registered-only`.
- Keep the default seed. It is the match impact report's sample seed, and the
  pick is the report's own seeded ordering, so the report's 30,000 cars are the
  first 30,000 of the slice: a baseline measured on live compares car by car
  with the local one. With another seed only a fraction of them is in the
  slice. The plan prints how many of the sample's cars the slice holds.
- A dry run exits non-zero when the plan has a problem, including a target that
  exists and is not a pilot build.
- The schema comes from the repo's migrations, not from a schema dump. Rows keep
  their ids (NOR ids included) and every sequence continues after the full
  build's last id.
- The build ends with a verification and exits non-zero when any check fails:
  each copied table has the same rows and the same content checksum as the
  source selection, the slice has exactly `--size` vehicles of the asked scope,
  the catalog batch is complete, and no reference points to a missing row.
  Ship only a database whose summary says every check passed.
- A verified build writes a manifest: seed, size, catalog batch, the time of
  the source snapshot and, per table, the row count and content checksum the
  verification computed. One copy is the `--manifest` file (default
  `<target-database>.manifest.json` in the working directory), one is stored in
  the pilot as `public.northstar_pilot_manifest`, so the dump carries it. A
  build with a failed check writes neither. The manifest holds counts and
  checksums only, no plate, VIN or vehicle id; keep it with the dump, not in
  Git.
- `--verify DATABASE --manifest FILE` builds nothing. It recomputes the counts
  and checksums of `DATABASE` (on the server `DATABASE_URL` points at, in a
  read-only session) and compares them with the manifest. Exit 0 when all are
  equal, 1 on any mismatch with the table named.

### What it contains

| Class | Tables | Rows |
|---|---|---|
| A, whole | reviewer rules and decisions (`match_resolution_rules`, `match_chunk_proposals`, `tecdoc_resolution_rules`, `translation_rule_versions`, rule drafts, `match_review_rule_decisions`), learned rules (`vehicle_enrichment_rules`), the TecDoc rule catalog, `match_chunk_builds`, `tecdoc_identity_registry`, `ingest_job_runs`, and the pinned catalog batch | all rows; the catalog tables only for the pinned batch |
| B, slice | `vehicles`, `vehicle_identifiers`, `vehicle_source_links`, `enrichment_ledger`; per TS record of a slice vehicle: `staging.transportstyrelsen_raw`, `normalization_results`, `vehicle_facts`, `match_field_resolutions`, `match_chunk_members`, `review_queue`; `match_chunks` with a slice member or a proposal | rows of the slice only |
| C, left out | match run telemetry, routing decisions, TecDoc staging tables, other catalog batches | none |

Chunk and build counters (`member_count`, `reason_profile`, `row_count`,
`chunk_count`) are recomputed for the slice. Counters that describe the full
build are copied as they are: rule `matched_rows`/`resolved_rows`, learned rule
`support`, and the record counts of `ingest_job_runs` (the batch pickers show
full-build counts).

### Dump, restore, verify

```sh
# Local: pg_dump from the Postgres container, so client and server versions match.
docker compose exec -T postgres pg_dump -Fc --no-owner --no-privileges \
  -U app -d northstar_pilot > northstar_pilot.dump
```

On live the pilot is loaded as a second database next to the running one. The
running database is not touched until the switch, and after the switch it stays
on the server under another name: that is the way back.

1. Copy `northstar_pilot.dump` and `northstar_pilot.manifest.json` to live
   (team-only transfer).
2. Create an empty database and restore into it. `--exit-on-error` matters:
   without it `pg_restore` carries on past a failed statement and still leaves
   a database behind.

   ```sh
   createdb -U app -T template0 northstar_pilot
   pg_restore --exit-on-error --no-owner --no-privileges \
     -U app -d northstar_pilot northstar_pilot.dump
   ```

   (`--single-transaction` instead of `--exit-on-error` also works and leaves
   nothing behind on a failure.) A non-zero exit means: drop `northstar_pilot`
   and start again.
3. `ANALYZE`. A dump carries no planner statistics; without them the first
   queries on the restored database plan badly.

   ```sh
   psql -U app -d northstar_pilot -c "ANALYZE"
   ```

4. Verify the restored database against the manifest, from `apps/backend` with
   `DATABASE_URL` pointing at live's server:

   ```sh
   python -m scripts.build_pilot_database --verify northstar_pilot \
     --manifest northstar_pilot.manifest.json
   ```

   Go on only when it exits 0. A `FAIL` line names the table whose rows or
   content differ from what was built.
5. Compare live's rule tables with the source (next section). Anything that
   exists only on live is not in the pilot and must be carried over first.
6. Switch by renaming, with the API stopped so no session holds either
   database. Run from the maintenance database (`postgres`), with `<live_db>`
   the database the API uses today:

   ```sql
   ALTER DATABASE <live_db> RENAME TO <live_db>_before_pilot;
   ALTER DATABASE northstar_pilot RENAME TO <live_db>;
   ```

   Set `NORTHSTAR_TECDOC_MATCH_CATALOG_BATCH` to the pinned batch and start the
   API. Do not empty or reload live's tables in place: the ledger and the
   vehicle tables refuse deletes.
7. The way back is the two renames in reverse. Keep `<live_db>_before_pilot`
   until the pilot has been accepted; dropping it is a separate, explicit
   decision.

### Compare live's rule tables before the switch

The restore does not merge: a rule, draft or reviewer decision that exists only
on live is not in the pilot. Run this query in live's current database and in
the source (the local full build; the pilot holds the same rule rows), and
compare the output line by line. Both sessions must render timestamps alike,
hence the `SET`.

```sql
SET TimeZone = 'UTC';
SELECT 'core.match_resolution_rules' AS rule_table, count(*) AS row_count,
       md5(string_agg(md5(to_jsonb(t)::text), ',' ORDER BY t.rule_id)) AS content
FROM core.match_resolution_rules AS t
UNION ALL
SELECT 'core.match_chunk_proposals', count(*),
       md5(string_agg(md5(to_jsonb(t)::text), ',' ORDER BY t.proposal_id))
FROM core.match_chunk_proposals AS t
UNION ALL
SELECT 'core.match_chunk_builds', count(*),
       md5(string_agg(md5((to_jsonb(t) - 'row_count' - 'chunk_count')::text), ','
                      ORDER BY t.build_id))
FROM core.match_chunk_builds AS t
UNION ALL
SELECT 'core.match_review_rule_decisions', count(*),
       md5(string_agg(md5(to_jsonb(t)::text), ',' ORDER BY t.decision_id))
FROM core.match_review_rule_decisions AS t
UNION ALL
SELECT 'core.tecdoc_resolution_rules', count(*),
       md5(string_agg(md5(to_jsonb(t)::text), ',' ORDER BY t.id))
FROM core.tecdoc_resolution_rules AS t
UNION ALL
SELECT 'core.tecdoc_rule_versions', count(*),
       md5(string_agg(md5(to_jsonb(t)::text), ',' ORDER BY t.rule_version))
FROM core.tecdoc_rule_versions AS t
UNION ALL
SELECT 'core.tecdoc_rules', count(*),
       md5(string_agg(md5(to_jsonb(t)::text), ',' ORDER BY t.rule_version, t.rule_id))
FROM core.tecdoc_rules AS t
UNION ALL
SELECT 'core.translation_rule_versions', count(*),
       md5(string_agg(md5(to_jsonb(t)::text), ',' ORDER BY t.version))
FROM core.translation_rule_versions AS t
UNION ALL
SELECT 'core.translation_rule_drafts', count(*),
       md5(string_agg(md5(to_jsonb(t)::text), ',' ORDER BY t.rule_id))
FROM core.translation_rule_drafts AS t
UNION ALL
SELECT 'core.manufacturer_entity_drafts', count(*),
       md5(string_agg(md5(to_jsonb(t)::text), ',' ORDER BY t.entity_id))
FROM core.manufacturer_entity_drafts AS t
UNION ALL
SELECT 'core.vehicle_enrichment_rules', count(*),
       md5(string_agg(md5(to_jsonb(t)::text), ',' ORDER BY t.rule_id))
FROM core.vehicle_enrichment_rules AS t
ORDER BY 1;
```

- An empty table shows a NULL `content`. The chunk build counters are left out
  of the hash because the pilot recomputes them for the slice.
- A line that differs means the two sides do not hold the same rows; it can
  also be only a counter (`matched_rows`, `support`) that live recomputed. Find the
  rows with the same key order, for example
  `SELECT rule_id, md5(to_jsonb(t)::text) FROM core.match_resolution_rules AS t ORDER BY 1`
  on both sides and a diff of the two outputs. Rows only on live: export them
  from live and import them into the full build, then cut the pilot again.
  Rows only in the source are the rules the pilot brings; that direction is
  expected, and `outputs/rules-2026-09-27/retire_on_live.json` lists what was
  retired on purpose.
- If live has `core.translation_rule_definition_versions` or
  `core.translation_rule_definitions` with rows, stop: the local full build has
  no such tables, so the pilot arrives with them empty. Those rows have to be
  exported from live and loaded after the switch.

### After go-live

Follow-up on the pilot, in this order:

1. `check-model-fills --retract`: takes back model fills the guard no longer
   accepts.
2. `apply-vehicle-rules`: applies the rules the pilot brought to the slice's
   cars.

Never run `learn-vehicle-rules` on a slice: it learns from the cars it sees, so
on 500,000 cars it would lose support for, and with `--activate` retire, rules
the full build supports. Rules are learned on the full build and shipped.

Also not for the pilot, because they assume the full build:
`build-match-chunks` (a newer build hides every reviewer rule),
`import-ais-vin-export` with another extract and `import-remote-passenger`
(both bring in cars outside the slice).

## Rollback

Check out the previously verified commit in `/opt/northstar` and run
`./infra/production/deploy.sh`. Persistent volumes are not replaced. Database
restoration is a separate, explicit operation and must use a verified backup.
