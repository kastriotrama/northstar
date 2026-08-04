# Normalization pipeline contract

SCRUM-87 establishes one deterministic contract for every normalization stage.
It extends the existing Transportstyrelsen normalizer; it does not replace raw
staging, PostgreSQL persistence, review routing, or job retry protection.

## Execution contract

NormalizationPipeline receives an explicit version and a set of transformers.
Every transformer has:

- a stable transformer ID;
- a unique numeric order;
- one deterministic apply operation.

The pipeline sorts by order before execution and rejects duplicate IDs, orders,
or any transformer that mutates the copied raw evidence. A transformer must not
read environment variables, perform database writes, or hide review decisions.
Database orchestration remains in normalization_service.py.

The initial TS sequence is:

1. initialize safe source metadata;
2. classify manufacturer;
3. create a model-family candidate;
4. extract production year;
5. translate transmission;
6. translate category-scoped Bodywork;
7. create drive candidates;
8. create fuel and electrification candidates.

## Normalized record contract

NormalizationOutcome contains:

- status: resolved, provisional, review-required, or failed;
- normalized accepted canonical values;
- proposed candidates that still require later acceptance rules;
- accepted and candidate rule IDs;
- review reasons;
- current confidence;
- pipeline version;
- an ordered decision trace.

Raw Transportstyrelsen rows remain unchanged in staging. The output never copies
plate or VIN values. Only the fact that those alias types exist may be retained.

## Decision trace contract

Every changed output field records:

- sequence number;
- transformer ID;
- target: normalized value, candidate, or review;
- field name;
- rule IDs;
- previous/source and new sanitized value;
- preliminary confidence effect.

Sensitive output field names such as plate and VIN are rejected by the trace
contract. Transformer source evidence is explicitly allow-listed and excludes
plate and VIN. Rule IDs are deduplicated without changing their first-seen
order.

Confidence effects explain whether evidence is supporting, neutral, or
conflicting. The existing v1 resolved/provisional/review confidence bands remain
authoritative until SCRUM-93 implements composite scoring and final routing
thresholds.

Example payload:

    {
      "sequence": 6,
      "transformer_id": "ts.transmission",
      "target": "normalized",
      "field": "transmission_type",
      "rule_ids": ["TRN-008"],
      "before": null,
      "after": "automatic",
      "confidence_effect": 0.1
    }

## Adding a transformer

1. Give it a stable ID and unused order.
2. Change only the shared in-memory context.
3. Record sanitized field changes and review reasons.
4. Add deterministic unit tests, including malformed and sensitive input.
5. Keep persistence and external integrations outside the transformer.
