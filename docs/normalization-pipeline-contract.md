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

The TS sequence from pipeline version 3 is:

1. canonicalize allow-listed text on an isolated working copy;
2. initialize safe source metadata;
3. classify manufacturer;
4. create a model-family candidate;
5. extract registration, production date, and explicit production ranges;
6. extract structured engine fields and normalize power/displacement units;
7. translate transmission;
8. translate category-scoped Bodywork;
9. create drive candidates;
10. normalize fuel carriers, fuel combinations, and electrification.

Dates are persisted in ISO form together with their source precision. Explicit
production ranges may be open-ended, but reversed or malformed ranges are not
partially accepted. Current TS `kw` values map to integer `power_kw`; current TS
`ccm` values map to integer `displacement_cc`. Inputs explicitly labelled as
metric horsepower (`power_ps`) use `1 PS = 0.73549875 kW`, and inputs explicitly
labelled in litres (`displacement_l`) use `1 litre = 1000 cc`. Half values round
up. Conflicting source units, non-positive numbers, unsupported dates, and
out-of-bound values route to review instead of being guessed.

Structured `engine_code`, `engine_family_code`, and `engine_family_name` values
are retained when the source provides them explicitly. Marketing model text is
never used to manufacture an engine identity. The decision trace preserves the
non-sensitive source value and the rule used for every accepted conversion.

Text canonicalization applies Unicode NFKC and whitespace cleanup. Registry
code fields are uppercased, while human names retain their source casing.
Typographic dashes and quotation marks are converted to their ASCII equivalents
only for name fields; punctuation is never removed. Plate, VIN, and unknown
fields are excluded. All later transformers read the working copy, while the
original staging evidence remains unchanged.

## Normalized record contract

NormalizationOutcome contains:

- status: resolved, provisional, review-required, or failed;
- normalized accepted canonical values;
- proposed candidates that still require later acceptance rules;
- accepted and candidate rule IDs;
- review reasons;
- preliminary Stage 1 confidence;
- pipeline version;
- an ordered decision trace.
- dictionary match records containing the exact rule version, source term,
  canonical value, and rule ID.

Raw Transportstyrelsen rows remain unchanged in staging. The output never copies
plate or VIN values. Only the fact that those alias types exist may be retained.

## Decision trace contract

Every changed output field records:

- sequence number;
- transformer ID;
- target: canonical working value, normalized value, candidate, or review;
- field name;
- rule IDs;
- previous/source and new sanitized value;
- preliminary confidence effect.

Sensitive output field names such as plate and VIN are rejected by the trace
contract. Transformer source evidence is explicitly allow-listed and excludes
plate and VIN. Rule IDs are deduplicated without changing their first-seen
order.

Confidence effects explain whether Stage 1 evidence is supporting, neutral, or
conflicting. Final identity confidence and resolved/provisional/review routing
are owned by the versioned
[confidence-routing contract](confidence-routing-contract.md); preliminary
Stage 1 bands do not override that gate.

Representative results across both stages are pinned by the sanitized, CI-enforced
[golden-corpus contract](golden-corpus-contract.md). Rule changes must expose their normalized,
routing and evidence differences before updated expectations can be approved.

The dictionary boundary and accepted/proposed behavior are defined in
[translation-dictionary-contract.md](translation-dictionary-contract.md).
Stage 2a candidate generation is defined separately in
[fuzzy-matching-contract.md](fuzzy-matching-contract.md); it consumes normalized
evidence and never mutates this Stage 1 pipeline result.

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
