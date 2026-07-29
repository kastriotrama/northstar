# Ingest Job Bookkeeping and Idempotency

| Field | Value |
|---|---|
| Version | `0.1` |
| Status | Accepted Phase 1 bookkeeping contract |
| Jira story | SCRUM-19 (Story 3.4) |
| Table | `core.ingest_job_runs` |

## Purpose

Every ingest operation is identified by `(job_name, batch_id)`. This pair is
unique and is the idempotency key for the operation. A caller must claim the
pair and commit that claim before performing source or graph writes.

Reclaiming a completed pair returns `should_execute = false`, so a completed
batch is a no-op. A running pair is not stolen. A failed pair may be reset and
retried with the same identity.

## Table contract

| Column | Meaning |
|---|---|
| `id` | Internal generated run identifier |
| `job_name` | Stable job name |
| `batch_id` | Source or caller batch identifier |
| `status` | `running`, `completed`, or `failed` |
| `records_processed` | Total attempted records |
| `records_succeeded` | Successfully handled records |
| `records_failed` | Failed records |
| `error_code` | Stable sanitized failure category |
| `error_summary` | Short sanitized operational explanation |
| `started_at` | Start or most recent retry time |
| `finished_at` | Completion/failure time; null while running |
| `updated_at` | Last state change |

The database enforces non-empty identifiers, the status vocabulary,
non-negative counts, `processed = succeeded + failed`, terminal timestamps,
and uniqueness of `(job_name, batch_id)`.

## Lifecycle and retry behavior

```text
new ──claim──> running ──complete──> completed ──reclaim──> no-op
                  │
                  └────fail───────> failed ──reclaim──> running
```

`claim_job_run` uses the unique job/batch constraint and row locking. Two
workers cannot safely claim the same active batch. `complete_job_run` and
`fail_job_run` record final counts and are idempotent when called again with
the same terminal state and counts.

Provider exception messages are deliberately not stored because they may
contain credentials or connection details. Failed runs instead require a
caller-supplied error code and sanitized summary (maximum 128 and 500
characters). Operational logs should use that category and the durable run id.

## Usage

Apply the schema:

```sh
northstar-ingest migrate-job-bookkeeping
```

Claim and commit before processing:

```python
claim = claim_job_run(connection, job_name="transportstyrelsen", batch_id=batch_id)
connection.commit()
if not claim.should_execute:
    return 0
```

After processing, call `complete_job_run` with balanced counts and commit. On
failure, call `fail_job_run` with the counts reached before the error and
commit. The raw staging and graph-write transactions remain separate from the
claim transaction so a worker crash cannot make an uncommitted claim appear
durable.
