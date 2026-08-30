# TS→TecDoc activation record — 2026-08-30

This records the controlled activation order requested for SCRUM-101. It does
not approve uncertain mappings, score the frozen holdout, persist TS match
decisions, attach aliases, or mutate Neo4j.

## Activated structural semantics

1. Official mixed TecDoc engine fuel is preserved as a set-valued fact. The
   scalar remains null and the source code, official label, representation and
   exact components remain auditable.
2. TS overlap with a mixed-fuel component set is compatibility-only. It adds no
   score and cannot confirm a KType; disjoint fuel remains a conflict.
3. A TS engine code is compared with the complete KType engine set. A hit may
   support matching, but a multi-engine KType remains candidate-only and no
   arbitrary engine is selected for Neo4j.
4. Full catalog rebuilds retain candidate-only KTypes for matcher evidence while
   sending only eligible, singular-engine promotions to the graph writer.

## Model/bodywork activation decision

- Volvo: **0 of 47** proposals activated. They are scoped bodywork
  compatibility proposals, not model mappings, and every item still has
  `requires_domain_review` evidence status.
- Golf: **0 new mapping rules** activated. The 143 independent RDW observations
  support the broad Golf family only. The existing normalized source model
  already generates that family query; the evidence does not identify a unique
  TecDoc KType or Variant and therefore must not add score.
- Frozen holdout: remains unscored until the final reviewed policy and acceptance
  criteria are immutable.

## Local catalog activation

Source contract:

- TecDoc version `0326`, format `2.70`
- 72,570 source KTypes
- SHA-256 `a96fa593a4cf18fc09b5fe5e0d8a62c7996c0430602d51f27856e603b7a77209`

The graph-safe-only diagnostic batch
`tecdoc-0326-all-active-candidates-v6-multifuel-local` proved that 57,613 KTypes
meet promotion gates and 1,805 of them use the new mixed-fuel representation.
It is not the matcher batch because it intentionally omitted candidate-only
rows. It remains immutable and is superseded for matching by
`tecdoc-0326-all-active-candidates-v6-multifuel-complete-local`, which preserves
all 72,570 KTypes and their graph-safety status. Neo4j writes are disabled for
both local rebuilds.

Remaining graph-ineligible reason counts from the source-wide gate are:

- 8,867 KTypes with multiple active engines
- 4,088 KTypes without defensible displacement
- 2,002 KTypes with unresolved or unmapped fuel

The complete batch passed count reconciliation and the same pinned 20,000-row
read-only replay. Compared with v5 on identical source row keys and rule pins:

| Terminal | v5 | v6 | Delta |
| --- | ---: | ---: | ---: |
| Resolved | 2,284 | 2,346 | +62 |
| Provisional | 2,218 | 1,883 | -335 |
| Review required | 13,788 | 14,068 | +280 |
| Hard conflict | 1,597 | 1,590 | -7 |
| Policy excluded | 112 | 112 | 0 |
| Normalization review | 1 | 1 | 0 |

There are 532 changed rows and 22 selected-KType changes. Important transitions:

- 222 provisional → resolved because their mixed-fuel KTypes are now graph-safe.
- 160 resolved → review and 117 provisional → review because newly represented
  mixed-fuel alternatives no longer receive an unjustified fuel-conflict
  penalty, reducing the winning margin below the acceptance gate.
- Four review → hard conflict, four hard conflict → provisional and seven hard
  conflict → review. The 22 selected-identity changes require case review.

The 160 lost resolutions repeat across 17 prior top KTypes, led by `000018113`
(71 rows) and `000018813` (43 rows). This is a focused adjudication population,
not evidence for relaxing the margin threshold.

Result: **catalog activation is held before the frozen holdout**. The structural
semantics and local immutable batch remain available for review, but there is no
decision-ledger persistence, alias attachment, Neo4j promotion or production
activation. Independent review must first confirm the 160 lost resolutions,
22 identity changes and four new hard conflicts. Only then may an immutable
accepted policy be frozen and the holdout scored once.

Private local evidence:

- `outputs/scrum101-multifuel-complete-20k-20260830.json`
- `outputs/scrum101-multifuel-catalog-comparison-20k-20260830.json`
