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
Transportstyrelsen source in batch `normalization-review-250-v4`. The current
result is 14 provisional and 236 review-required records, with no technical
failures. Those review-required records are useful evidence: they expose the
manufacturer and other source ambiguities that the next rule and matching work
must resolve rather than silently accepting weak data.
