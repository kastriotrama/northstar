# Mixed-fuel catalog adjudication — 2026-08-30

This is a local, read-only review package. It compares the complete v5 and v6
TecDoc candidate catalogs over the same first 20,000 retained TS rows with the
same source and rule pins. It does not approve mappings or write PostgreSQL,
Neo4j, the match ledger, or aliases.

## Reproduced controls

Both runs use matcher digest
`fe252a5b5972959e06ba10aa54a3a4a09b1ce8ffb39af7d7627c2d7a149fbb6b`, source
digest `b6ccfcdce5ebebaa1c0817f12ed49f71d6351f42de9edcceec3a485391b4e8ca`,
rule version `ts-engine-fingerprint-heldout-v1-disabled-20260824`, and 72,570
catalog candidates.

| Terminal | v5 control | v6 complete catalog | Delta |
| --- | ---: | ---: | ---: |
| Resolved | 2,284 | 2,346 | +62 |
| Provisional | 2,218 | 1,883 | -335 |
| Review required | 13,788 | 14,068 | +280 |
| Hard conflict | 1,597 | 1,590 | -7 |
| Policy excluded | 112 | 112 | 0 |
| Normalization review | 1 | 1 | 0 |

The comparison has 532 changed rows and 22 selected-candidate identity changes:

- 222 provisional → resolved
- 160 resolved → review required
- 117 provisional → review required
- 4 review required → hard conflict
- 4 hard conflict → provisional
- 7 hard conflict → review required
- 13 other hard-conflict rows retained a hard-conflict terminal but changed
  selected candidate identity
- 5 review-required rows retained their terminal but changed selected identity

## What the new fuel semantics changed

The v6 catalog preserves mixed TecDoc fuel as a set-valued fact. A TS fuel that
overlaps a mixed set is compatibility-only; it is not treated as confirmation.
Disjoint fuel remains a hard conflict. The v6 catalog also keeps candidate-only
KTypes for matcher evidence without promoting an ambiguous engine to the graph.

The 160 lost resolutions are therefore not evidence that the margin gate should
be relaxed. They are the population where the corrected alternatives reduce the
winning margin or expose a fuel conflict. The repeated v6 top candidates in the
full review packet are:

| KType | Changed rows |
| --- | ---: |
| `000018113` | 71 |
| `000018566` | 43 |
| `000018813` | 43 |
| `000018567` | 35 |
| `000018040` | 30 |
| `000019008` | 24 |
| `000057401` | 19 |
| `000018333` | 19 |

The v6 after-state contains 13 fuel-conflict cases and four power conflicts in
the changed population. The review packet retains the exact TS measurements,
normalized values, all matcher attempts, candidate evidence, and the old/new
terminal decision for each row. Its audit found 216 resolved gains with five
technical fields and 6 with four or six fields; every verdict remains unset.

## Required domain decisions

1. For each repeated KType cohort, confirm whether the TS vehicle identity is
   compatible with the v6 mixed-fuel set and whether the lost v5 resolution was
   actually overconfident.
2. Independently adjudicate the 22 candidate-identity changes and all hard or
   provisional conflict transitions; do not infer correctness from the matcher
   output alone.
3. Approve or reject a versioned mixed-fuel acceptance policy. Only an approved
   policy can unblock the frozen holdout, immutable SCRUM-171 decisions,
   SCRUM-170 alias attachment, and Neo4j reconciliation.

Private evidence files:

- `outputs/scrum101-multifuel-v5-current-20k-20260830.json`
- `outputs/scrum101-multifuel-v6-current-disabled-20k-20260830.json`
- `outputs/scrum101-multifuel-catalog-comparison-current-20k-20260830.json`
- `outputs/scrum101-multifuel-catalog-all-change-review-packet-20260830.json`
- `outputs/scrum101-multifuel-catalog-all-change-review-audit-20260830.json`

These files contain real plate values and must remain in the restricted local
workspace.
