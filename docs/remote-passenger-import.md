# Reproducible VD-AI passenger import

The shared full-source contract imports exactly **6,515,471** passenger vehicles
from `public.swedish_vehicles`, ordered by plate. Passenger scope is:

- `eu_category` equal to `M1` or `M1G`; or
- missing `eu_category` with `vehicle_type` equal to `PB`.

The command verifies the remote count before staging any rows. A different count
stops the run, preventing developers from silently importing different snapshots.

## Configuration

Set these locally or in the runtime secret manager; never commit credentials:

```bash
DATABASE_URL=postgresql://...
REMOTE_DATABASE_URL=postgresql://...
```

Apply the standard PostgreSQL migrations first, then run:

```bash
northstar-ingest import-remote-passenger \
  --prefix normalization-vdai-passenger-full-v323-20260817 \
  --batch-size 25000 \
  --expected-source-count 6515471 \
  --retain-raw
```

The fixed prefix and plate ordering make checkpoints portable and retries
idempotent. Each completed part contains 25,000 rows except the final part. The
checkpoint table is `core.remote_passenger_import_parts`.

## Recovery

Only when no other worker owns the prefix, add `--recover-stale-part`. It deletes
the staging, normalization, review, and job-bookkeeping artifacts for the single
next uncheckpointed part, then refetches that part. Completed checkpoints are never
changed.

```bash
northstar-ingest import-remote-passenger \
  --retain-raw \
  --recover-stale-part
```

Do not run two workers with the same prefix. A production scheduler should enforce
singleton execution.

## Progress

```sql
SELECT count(*) AS completed_parts,
       sum(source_count) AS completed_cars,
       sum(resolved) AS resolved,
       sum(provisional) AS provisional,
       sum(review_required) AS review_required,
       sum(failed) AS failed
FROM core.remote_passenger_import_parts
WHERE import_prefix = 'normalization-vdai-passenger-full-v323-20260817';
```

Normalization results other than review/failed are pruned after their aggregate
checkpoint is stored. `--retain-raw` keeps every source record for later integrated
TS-to-TecDoc matching.
