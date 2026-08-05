# Translation dictionary contract

SCRUM-89 moves the reviewed Transportstyrelsen mappings into immutable,
exactly addressable rule sets. The current `ts-translation-v4` rules incorporate
the latest bodywork comments from the stakeholder workbook
`translation-rules-review-transmission-fuel-bodywork with comment.xlsx`.

## Loading and decisions

Callers request one exact version through `load_translation_rule_set(version)`.
There is no `latest` alias and no fallback to another version. Each rule has a
stable ID, source fields and terms, canonical target, decision, and any required
manufacturer or vehicle-category scope. Rules are sorted by ID and duplicate
IDs fail during construction.

An `accepted` rule may populate normalized output only after all its conditions
are satisfied. A `proposed` rule cannot populate normalized output. The one
undecided workbook row, `FUEL-000`, remains proposed and represents absence; it
never creates `unknown`, `zero`, or another fuel value.

## Stakeholder changes represented in version 2

- Fuel code 3 remains the carrier `electricity`; `EV / Electric / El` are
  display/search terms because electrification is a separate concept.
- Gengas is stored as `gengas`.
- Motorgas and natural gas are stored as `cng`.
- Rapeseed oil is stored as `rme`.
- Biogas is stored as `renewable_cng`, displayed as rCNG / Renewable CNG.
- Biodiesel is stored as `diesel`.
- Bodywork BA is stored as `truck`.
- Bodywork BB and reviewed van marketing remain `van` internally.
- European/British `estate` is the canonical form; `wagon` remains an input and
  search synonym rather than a stored value. Legacy Lastkombi uses
  `cargo_estate`.
- Passenger-van marketing terms use `passenger_van`.
- Official passenger code AF remains `multi_purpose_vehicle`; when AF and a
  compatible Passenger Van marketing term occur together, the official AF value
  wins without creating a false conflict.

Versions 2 and 3 remain loadable for deterministic replay. Version 2 retains
the earlier `BDY-010` value `multi_purpose_vehicle`; version 3 introduces
`passenger_van` while retaining `wagon`; version 4 uses the reviewed European
`estate` terminology.

## Matching safety

Structured TS codes outrank marketing text. Marketing rules require the
reviewed manufacturer and/or vehicle-category scope. If structured and
marketing evidence disagree, the structured value is retained but the record
is routed to review. Unknown codes do not fall through to a marketing guess.
Motorhome marketing text still requires SA, legacy 08, or equivalent supporting
evidence before it can become accepted.

Fuel carriers, fuel combination, and electrification are separate fields. A
hybrid or plug-in-hybrid configuration requires electricity plus a combustion
carrier. Battery-electric requires electricity without a combustion carrier.
Conflicting configurations route to review.

## Audit record

Every applied dictionary match records:

- rule-set version and rule ID;
- accepted or proposed decision;
- matched source field and term;
- canonical target field and value.

The match record is persisted inside the normalized payload alongside the
ordered decision trace. Plate and VIN are rejected as match source fields.
