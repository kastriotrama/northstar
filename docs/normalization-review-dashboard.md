# Normalization review dashboard

The normalization review dashboard is an operator-facing view of the latest
Transportstyrelsen normalization batch. It is intentionally read-only: using
the screen cannot change a source record, approve a candidate, or write to the
vehicle graph.

## What an operator can inspect

- batch totals split into resolved, provisional, needs-review, and failed;
- up to 300 vehicles per request (250 by default);
- search across normalized manufacturer, model family, and engine code;
- filters for status, manufacturer, bodywork, fuel, and transmission;
- the normalized fields, confidence, review signals, decision trace, and
  applied translation rules for the selected record;
- combined filters, pagination, keyboard row selection, and `Cmd/Ctrl+K`
  search focus.

Only the sanitized normalization result is returned by the API. Raw staging
payloads, registration plates, VINs, and other source identifiers are not
returned to the browser.

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

The development database contains 250 deterministic samples from the supplied
Transportstyrelsen source. The latest batch, `normalization-review-250-v5`, was
reprocessed with `normalization-pipeline-v4`: 7 records resolved, 81 are
provisional, 162 require review, and none failed technically. Compared with the
previous rules, 74 records left review without making any previously
provisional record worse. The remaining review records expose missing
manufacturer evidence and unresolved vehicle-category bodywork codes rather
than silently accepting weak data.
