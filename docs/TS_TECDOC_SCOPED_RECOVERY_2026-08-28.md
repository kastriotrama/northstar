# Scoped family recovery and KType readiness — 2026-08-28

Continues the [immediate package](TS_TECDOC_IMMEDIATE_PACKAGE_2026-08-28.md)
in `NorthStar-SCRUM-101-integrated`, branch
`feature/SCRUM-101-integrated-matcher-validation`. Local only; no commit/push,
datastore writes, actual rule activation or invented independent verdicts.

## Software delivered

`ingestion/tecdoc/source_model_rules.py` adds a frozen, versioned source-context
policy for querying an exact canonical catalog family. The actual
`TecDocDryRunEvaluator` consumes this optional policy; defaults remain empty.

- A rule requires manufacturer, explicit raw source model, exact target family,
  approval plus type-text or variant/version conditions, reviewer and evidence.
- Bodywork alone cannot establish model identity. Source conditions are literal
  values; there are no wildcard/prefix approval matches or stripped extensions.
- The target must exist as an exact canonical family in the pinned manufacturer
  catalog, not merely a trim alias. Conflicting source assertions or opposing
  rules fail closed.
- The rule supplies a query, never a KType ID. All candidates still compete
  through existing fuel/year/engine/power/displacement/bodywork/drive and margin
  gates. Ambiguous results cannot fall back to a broader, higher-scoring model.
- Candidate-only results remain provisional. Rule IDs and policy content digest
  are included in evaluation evidence, and the cache separates applied rules.
- The local validation CLI supports `--source-model-policy`,
  `--source-model-policy-version`, `--source-model-policy-sha256`; all three are
  required together. Proposed manifests fail closed. Approved metadata is an
  explicit operator input, not an independently authenticated approval workflow.
- Existing replay tools reject active source-policy reports they cannot load;
  they do not silently replay with the policy omitted.

Real source-model rules activated: **zero**. Synthetic tests demonstrate the
mechanism, not approval of any Golf mapping.

## Golf: independent evidence is still missing

The 143 targeted cases are not a single uniform source type:

| Raw type text | Records |
|---|---:|
| AU | 66 |
| AUV | 59 |
| CD | 16 |
| CDV | 2 |

The development 20k contains 718 Golf source observations. Explicit source text
provides eight `GOLF VARIANT` anchors (five with approval identifiers), 35
`GOLF PLUS` and 18 `GOLF SPORTSVAN` anchors. We reused the existing strict full
model-fingerprint proposal engine with catalog gating and repeated-vehicle
deduplication. **Zero repeated, uniquely anchored proposals qualified.**

This result is specific to the full fingerprint profile and current development
population; it is not proof that no authoritative mapping exists elsewhere.
No candidate winner, AC body code or engine allocation was used as an
independent model label. The next step is an independent approval/type → family
reference or a separate development evidence cohort. Do not learn these rules
from the frozen holdout below, and do not globally alias `GOLF` to `GOLF VARIANT`.

## The 114 provisional matches: exact readiness blockers

They reference **11 distinct KTypes**, not 114 separate catalog objects.

| KType | Cars | Stored blocker |
|---|---:|---|
| 000018566 | 43 | Engine fuel unresolved |
| 000018567 | 35 | Engine fuel unresolved |
| 000016825 | 10 | Engine fuel unresolved |
| 000015820 | 8 | Engine fuel unresolved |
| 000017355 | 7 | Engine fuel unresolved |
| 000016826 | 3 | Engine fuel unresolved |
| 000019772 | 3 | Engine fuel unresolved |
| 000018568 | 2 | Engine fuel unresolved |
| 000008775 | 1 | Engine fuel unresolved |
| 000134196 | 1 | Engine fuel unresolved |
| 000026594 | 1 | Two engine allocations |

The first ten KTypes cover 113 cars. Their single engine allocations use KT 088
code `026`, officially **Petrol/Alcohol**, read from the local TecDoc reference
tables. This is not an omitted plain-petrol spelling: the scalar canonical engine
fuel mapper does not represent this mixed classification. Vehicle-level petrol
and TS fuel agreement do not justify replacing that engine fact with petrol or
assuming alcohol means ethanol/E85.

KType `000026594` has both `Z 19 DTR` and `A 19 DTR` allocations. Neither is
selected arbitrarily. All 11 also lack an exact from/to engine displacement;
the pinned candidate catalog has a single displacement value per relevant engine,
but complete-source verification is still required before using that consensus
under the existing promotion path. Independent confirmation and explicit
promotion remain required for every case.

Read-only Neo4j lookup found **zero existing KType alias → VehicleVariant targets
for these 11 KTypes**. No existing provisional graph target can simply be
unlabelled. An approved catalog/engine representation and audited graph import
must precede TS alias attachment. No promotion writer was invoked by this audit.
The readiness report is diagnostic; it is not a new transactional enforcement
gate in the production promotion writer.

Next engineering decision under SCRUM-170: define how the graph preserves
mixed engine-fuel capability without fabricating a scalar fuel, verify complete
source displacement and keep multiple engine allocations intact. Then rebuild
a new pinned catalog batch and revalidate; do not mutate the old batch.

## Volvo review manifest

Prepared **47 proposed rules**, each scoped to exact manufacturer/catalog family,
`AC`, normalized estate and one literal TS approval identifier. Coverage:

- 403 of 406 targeted Volvo records: 378 reviews and all 25 hard conflicts.
- Three reviews lack approval identifiers and are deliberately uncovered.
- The 25 power/year conflicts remain in the evidence; vocabulary compatibility
  does not clear them. One drive disagreement also remains visible.
- `status=proposed`, empty reviewer/evidence approval fields. The production
  loader rejects this manifest. Compatibility would be non-confirming, not an
  extra positive score, if subsequently approved and validated.

Private combined evidence:
`outputs/scrum101-next-repair-proposals-20260828.json`. This includes the 143 Golf
source cases, per-KType source/engine evidence, graph snapshot and Volvo manifest.
The preceding full bodywork sibling packet remains available for candidate detail.

## Frozen source-only holdout

Scanned the next ordered **50,000 raw rows** after the pinned development 20k.
Excluded groups linked by normalized VIN, plate, approval family (including
revisions), or variant/version. Links are transitive, preventing a repeated
vehicle from bridging two splits. Source strata retain vehicle type, primary
fuel code and presence/absence of model without consulting matching outcomes.

- **11,629 records**, **11,107 groups** retained.
- **38,371** excluded as linked to the current development cohort.
- No matcher outputs or labels were produced or used for this selection.
- Private source/row/group hashes and source IDs are frozen in
  `outputs/scrum101-source-only-holdout-50k-window-20260828.json`.

This is disjoint from the current 20k under the stated keys, not a random,
nationally representative 50k sample or proof of independence from every earlier
experiment. Check any other development datasets against it before validation.
Retain it unscored until rules and acceptance criteria are frozen.

## Validation and rollout boundary

699 unit tests, 205 golden cases, Ruff and strict mypy (119 source files) pass.
Actual local PostgreSQL and read-only Neo4j audit completed. The source-only
holdout freeze also completed; no migration or graph-write integration run is
claimed. No new dependencies were installed.

The full default-disabled-policy replay completed in 627.7 seconds. All 20,000
complete evaluations (including reasons and KType references) are identical to
the preceding Saab report: **2,284 resolved, 2,218 provisional, 13,788 review,
1,597 hard conflicts, 112 policy exclusions, one normalization review**. Zero
failures, changed identities or regressions. No accuracy or resolution uplift
is claimed from inactive rules.

Report: `outputs/scrum101-source-model-policy-disabled-20k-20260828.json`.
SHA-256: `6d822a325734cae383fc9b1859a8d40344af7a25b04e9c88f50efeeea234c40d`.
Progress/evidence comments were added to SCRUM-170, SCRUM-173, SCRUM-174 and
SCRUM-175; issue statuses remain unchanged.

Next approved sequence:

1. Independently establish Golf approval/type → family mappings and review the
   Volvo manifest. Keep uncertain verdicts explicit.
2. Complete mixed-fuel/engine/displacement evidence design for the 11 KTypes.
3. Pin approved rules, compare the exact 20k, then evaluate the frozen holdout.
4. Only after acceptance: SCRUM-171 immutable decisions, SCRUM-170 controlled
   promotion/attachment, PostgreSQL/Neo4j reconciliation and frontend delivery.
