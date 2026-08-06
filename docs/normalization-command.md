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
- Accepted manufacturer, bodywork, transmission, date, engine, power, and
  displacement decisions
  may populate normalized fields.
- Stakeholder-approved transmission, fuel, electrification, and bodywork rules
  from `ts-translation-v4`
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
Owner fields are not parsed. The first review cohort is intentionally limited
to passenger cars: records with an explicit EU category must be `M1`/`M1G`;
when that category is absent, the TS vehicle type must be `PB` (`personbil`).
Trucks, buses, trailers, motorcycles, tractors, and other categories are
excluded before staging and normalization.

The SCRUM-82 pilot workbook is redacted and intended for rule review. Its
coverage numbers describe outputs or candidates, not automatically approved
canonical values.

## Populate a test database from the portable Excel bundle

The PR includes a public-safe workbook whose registration plates and VINs are
deterministic synthetic values. VIN WMI prefixes and identifier presence are
preserved, so the workbook produces the same normalization decisions without
publishing real source identifiers.

Install the PR version, start an isolated PostgreSQL database, and run:

```bash
northstar-ingest import-normalization-bundle \
  --file outputs/019fadda-d238-75d3-8312-142dfdce2612/northstar_normalization_test_bundle_2026-08-06.xlsx
```

Three larger, mutually exclusive passenger-car cohorts are also included for
testing different environments:

| Cohort | File | Resolved | Provisional | Review required | Failed |
|---|---|---:|---:|---:|---:|
| Alpha | `northstar_ts_normalization_alpha_1000_2026-08-06.xlsx` | 43 | 890 | 67 | 0 |
| Bravo | `northstar_ts_normalization_bravo_1000_2026-08-06.xlsx` | 59 | 855 | 86 | 0 |
| Charlie | `northstar_ts_normalization_charlie_1000_2026-08-06.xlsx` | 63 | 876 | 61 | 0 |

All four workbooks are stored under
`outputs/019fadda-d238-75d3-8312-142dfdce2612/`. Run the same command with the
desired filename. The three 1,000-car files can coexist in one database because
their source batches and vehicle cohorts do not overlap.

The command performs the complete bootstrap rather than merely copying cells:

1. validates every required sheet, JSON payload, batch/record relationship,
   application catalog, base rule version, and override layer;
2. applies the staging, review-queue, job-safety, and normalization migrations;
3. inserts the exact immutable rule-version row and sanitized TS staging rows;
4. runs normalization with the workbook's effective translation and
   manufacturer rules;
5. compares every generated payload, status, rule list, review reason, and
   confidence value with the `Normalized Results` sheet;
6. prints a JSON summary and exits with code `0` only after exact verification.

The operation is idempotent: retrying an already verified bundle succeeds
without duplicating records. It rejects a conflicting batch, rule version,
application catalog, mapping version, pipeline version, or result payload.
Run it only in a disposable local or CI test database, never production.
