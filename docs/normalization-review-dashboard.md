# Normalization review dashboard

The normalization review dashboard is an operator-facing view of the latest
Transportstyrelsen normalization batch. It is intentionally read-only: using
the screen cannot change a source record, approve a candidate, or write to the
vehicle graph.

## What an operator can inspect

- batch totals split into resolved, provisional, needs-review, and failed;
- up to 300 vehicles per request (250 by default);
- search across source Brand, normalized manufacturer, model family, and engine
  code;
- filters for status, manufacturer, bodywork, fuel, and transmission;
- the normalized fields, confidence, review signals, decision trace, and
  applied/candidate rule provenance for the selected record;
- combined filters, pagination, keyboard row selection, and `Cmd/Ctrl+K`
  search focus.

Only the sanitized normalization result is returned by the API. Raw staging
payloads, registration plates, VINs, and other source identifiers are not
returned to the browser. Brand is the only newly exposed source field because
it is required to audit manufacturer decisions; source record ID remains the
non-sensitive way to identify an example in the review screen.

## Run locally

Start PostgreSQL and apply the existing staging and normalization migrations,
then import and normalize a Transportstyrelsen batch. Start the API with:

```sh
uvicorn api.app.main:app --reload
```

Open <http://localhost:8000/normalization-review>. The dashboard automatically
selects the most recently created normalized batch. The JSON endpoint is:

```text
GET /v1/normalization-review/vehicles
```

Supported query parameters are `query`, `status`, `manufacturer`, `bodywork`,
`fuel`, `transmission`, `batch_id`, `limit`, and `offset`. `limit` is capped at
300 so a browser review remains responsive.

## Current local review sample

The development database contains a deterministic 250-passenger-car sample
from the supplied Transportstyrelsen source. The latest batch,
`normalization-review-passenger-250-v1`, contains 243 `M1` and 7 `M1G` records.
After the approved Manufacturer entity Brand-prefix re-import, 37 are resolved,
207 are provisional, 6 require review, and none failed.
Every staged row has TS vehicle type `PB`; trucks, buses, trailers,
motorcycles, tractors, and other categories are excluded before normalization.
The remaining six review cases are protected corporate-group, converter, legal
entity, or ambiguous-marque decisions. This cohort has no bodywork/category
conflicts.
