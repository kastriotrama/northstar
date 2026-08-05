# Confidence scoring and routing contract

SCRUM-93 defines the final Phase 1 gate between Stage 2 candidate generation
and later graph/review writers. The gate calculates and persists an explainable
identity confidence decision. It does not itself mutate Neo4j.

## Composite confidence

Policy `confidence-routing-v1` uses constructor-injected thresholds and these
default weights:

| Signal | Weight | Value |
|---|---:|---|
| Model text similarity | 0.50 | Stage 2 edit/token score |
| Manufacturer scope | 0.20 | exact `1.0`, fuzzy `0.65`, phonetic `0.50`, global `0.0` |
| Context consistency | 0.20 | ratio of matching year/fuel/engine evidence; missing candidate evidence is neutral at `0.5` |
| Candidate separation | 0.10 | top-to-runner-up margin relative to the configured minimum margin |

Each weighted contribution is rounded to six decimal places and retained in an
ordered decision trace. The sum is clamped to `0..1`. When no candidate exists,
confidence is `0` and the route is review.

## Routing boundaries

The Phase 1 defaults are:

| Route | Statistical boundary |
|---|---|
| `resolved` | confidence `>= 0.90` |
| `provisional` | confidence `>= 0.70` and `< 0.90` |
| `review_required` | confidence `< 0.70` or no candidate |

Threshold equality is inclusive in the safer higher route: exactly `0.90` is
resolved and exactly `0.70` is provisional. Thresholds and weights are one
validated policy object rather than scattered constants.

Statistics never override safety gates. Manufacturer, fuel, engine, numeric
model-series and production-year conflicts route to review. Non-exact
manufacturer scope, phonetic-assisted model evidence and an insufficient
top-candidate margin also route to review regardless of score. A review route
never stores a selected candidate.

## Persistence and provenance

`core.match_routing_decisions` stores one immutable decision per source record,
candidate-catalog version and policy version. The deterministic UUID also
includes source system and batch. A retry is accepted only when the complete
payload is identical.

The persisted payload contains:

- policy version, route and composite confidence;
- selected candidate for resolved/provisional routes and the top candidate for
  explanation;
- reason codes and hard conflicts;
- every weighted trace entry and routing rule;
- ordered alternative candidate payloads, including fuzzy/phonetic provenance.

Database checks enforce valid routes, confidence range, selected-candidate
semantics, JSON trace/alternative shapes and source-version uniqueness. The
writer accepts only an existing `staging.<entity>` row in the stated batch.
Plate, VIN and raw source payloads are not copied into the decision.

Apply the schema with:

```bash
northstar-ingest migrate-confidence-routing
```

The supplied candidate catalog must have an explicit immutable version. Loading
the real TecDoc catalog and writing accepted decisions to Neo4j are separate
workflows.
