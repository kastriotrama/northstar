# TS→TecDoc mixed-fuel remaining-change adjudication

Date: 2026-08-31

This review covers the 504 v5→v6 changed cases that do not touch a hard-conflict
terminal. Together with the separate 28-case hard-conflict manifest, every
changed development case is now classified. Approval is for one frozen holdout
experiment only; it is not direct production or KType identity approval.

## Stable-identity eligibility gains: 222

All 222 cases retain the same KType. The v5 matcher already reached resolved
scoring but forced provisional because the KType was not graph-safe. The v6
catalog preserves set-valued source fuel evidence and makes the same singular
identity graph-safe.

Recommendation accepted: allow these gains in the candidate holdout policy.
They cannot be persisted or promoted from the development review alone.

## Conservative downgrades: 277

- 160 resolved → review-required, same KType.
- 117 provisional → review-required, same KType.

Corrected mixed-fuel alternatives reduce the winning margin. Keeping the old
terminal would assert more certainty than the candidate evidence supports.

Recommendation accepted: retain every downgrade. Do not relax candidate margin
or compatibility thresholds to recover the old count.

## Rejected unresolved identity changes: 5

Four changes prefer LPG-labelled KTypes for TS petrol records. LPG candidates
may contain petrol as a component, but that compatibility does not prove the
vehicle is the LPG KType. The fifth proposes an Audi Q8 SUV for an A8 sedan.

Recommendation accepted: reject all five candidate identity changes. Preserve
review-required routing with no selected identity.

## Combined development decision

- 532/532 changed cases classified across the two reviewed manifests.
- 222 stable-identity gains may enter the frozen holdout candidate policy.
- 288 conservative conflict removals/downgrades are accepted without direct
  identity approval: 11 fuel-conflict removals plus 277 margin downgrades.
- 22 candidate identity changes are rejected: 17 hard-conflict-derived and five
  unresolved changes.
- No development decision, alias or graph assertion may be written before the
  holdout passes and the final pins are frozen.
