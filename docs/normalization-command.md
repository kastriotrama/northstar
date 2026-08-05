# Transportstyrelsen normalization command

`northstar-ingest normalize` reads one already-loaded
`staging.transportstyrelsen_raw` batch and writes versioned, sanitized results
to `core.normalization_results`.

Run it with the exact source batch:

```bash
northstar-ingest normalize --batch-id <source-batch-id>
```

The batch ID is required so the command never normalizes an accidental or
implicit data set. The command applies its PostgreSQL dependencies, claims the
job through `core.ingest_job_runs`, processes staging rows in keyset-paginated
chunks, and commits each chunk. A completed retry is a no-op. A failed retry
reuses deterministic normalization and review IDs.

## Safety behavior

- Raw staging rows are read-only.
- Every output field change records its ordered transformer, rule IDs,
  sanitized before/after values, and preliminary confidence effect. See
  [the normalization pipeline contract](normalization-pipeline-contract.md).
- Plate and VIN values are never copied into normalized results, review items,
  logs, or pilot reports.
- Accepted manufacturer, bodywork, transmission, and production-date decisions
  may populate normalized fields.
- Stakeholder-approved fuel and electrification rules from `ts-translation-v2`
  populate normalized fields only when their structured evidence is complete.
- Drive, model-family, and brand-based matches remain candidates.
- Proposed, unknown, or conflicting translation evidence routes to review and
  cannot silently enter accepted output.
- Unknown manufacturers never fall back to
  `Tillverkare grundfordonet` merely because it is populated.
- Recognized converters use a recognized base manufacturer while retaining the
  converter separately.
- Unresolved or malformed records enter `core.review_queue`.
- This command does not write to Neo4j or Elasticsearch.

## Pilot source

`ingestion.transportstyrelsen_pilot` can select a deterministic cohort from a
true Transportstyrelsen fixed-width vehicle export and load only approved
vehicle fields. Records shorter than the required vehicle layout are rejected.
Owner fields are not parsed.

The SCRUM-82 pilot workbook is redacted and intended for rule review. Its
coverage numbers describe outputs or candidates, not automatically approved
canonical values.
