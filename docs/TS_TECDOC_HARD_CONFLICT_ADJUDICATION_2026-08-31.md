# TS→TecDoc mixed-fuel hard-conflict adjudication

Date: 2026-08-31

This review covers every one of the 28 v5→v6 changed cases whose before or
after terminal is `hard_conflict`. The product owner authorized the conservative
recommendations during the review. The review does not approve an individual
KType match and is not independent external ground truth.

## Approved conflict removal: 11 cases

Four Audi cases move from hard fuel conflict to provisional. Seven Audi,
Mercedes-Benz, Land Rover and Volvo cases move from hard fuel conflict to
review-required. In every case, the TS single fuel is an exact component of the
TecDoc mixed-fuel set and the selected KType does not change.

Recommendation accepted: remove the false disjoint-fuel conflict. Mixed-fuel
overlap remains compatibility-only and adds no score. Preserve the v6
provisional/review terminal; this adjudication cannot promote a vehicle.

## Rejected new Peugeot hard conflicts: 4 cases

The four records are Peugeot 3008 vehicles with TS version `HNSU-C16E00`,
petrol, 1,199 cc and 96 kW. V5 led with KType `000121650`, a 3008 II petrol
candidate with HNS/HNY engines and 96 kW. V6 instead led with `000156880`, a
3008 III hybrid candidate with HPY engine and 100 kW, then declared a power
conflict.

Recommendation accepted: reject both the candidate change and the derived hard
conflict. Keep the records unresolved and add technical-signature
discrimination before reconsidering them.

## Rejected MINI petrol hard conflicts: 13 cases

TS identifies 2020–2022 electric MINI Cooper SE vehicles. V6 changes the top
candidate from future-year electric J01 `000156380` to petrol F56 `000100572`
and reports a fuel conflict. The same catalog contains electric F56 candidate
`000136727`, but current model ranking does not select it safely.

Recommendation accepted: reject the petrol candidate and its hard conflict.
Keep all 13 unresolved. Investigate a reviewed approval/variant/model bridge to
the electric F56 family; do not attach either conflicting KType.

## Result and remaining gate

- 28/28 hard-conflict-touching changes are classified.
- 11 false fuel conflicts are approved for removal without identity approval.
- 17 candidate-derived hard conflicts are rejected and remain unresolved.
- No match identity, engine, score increase, alias or Neo4j promotion is
  approved.
- The remaining 504 changed cases still require cohort review before the full
  mixed-fuel v6 policy can be approved.

The versioned decision is
`ingestion/reviewed_adjudications/mixed_fuel_v6_hard_conflicts_v1_20260831.json`.
Its source is pinned to the private 532-case packet checksum; the manifest
contains no plates or VINs.
