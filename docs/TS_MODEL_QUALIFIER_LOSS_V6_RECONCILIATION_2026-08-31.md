# Model qualifier loss: final v6 reconciliation

Date: 2026-08-31  
Story: SCRUM-101  
Status: local, read-only evidence; no mapping activation

## Outcome

The remote qualifier-loss measurement is valid at the normalization boundary,
but it does not by itself establish a current matcher defect. The final v6
matcher already considers the raw registry model as catalog-gated evidence, so
many discarded qualifiers are recovered during candidate discovery without
weakening the partial-model or conflict gates.

The reproducible audit in `scripts/audit_model_qualifier_loss.py` verified the
exact source, candidate catalog, and normalization rule digests from
`scrum101-final-policy-v6-active-hns-20k-20260831.json`. It read local
PostgreSQL in a repeatable-read, read-only transaction and performed no
PostgreSQL or Neo4j writes.

## Final v6 20,000-row result

| Measure | Rows |
|---|---:|
| Source rows | 20,000 |
| Rows carrying registry model text | 11,106 |
| Strict trailing qualifier dropped by normalization | 1,195 |
| No more-specific catalog family explicitly present in source text | 764 |
| More-specific family ambiguous | 1 |
| Unique more-specific catalog family explicitly present | 430 |

The 430 unique-family observations finish as:

| Terminal | Rows |
|---|---:|
| Resolved | 189 |
| Provisional | 59 |
| Review-required | 172 |
| Hard conflict | 10 |

The most frequent unresolved reason occurrences are 81 bodywork conflicts, 69
candidate-margin failures, 12 source-model evidence conflicts, 10
partial/phonetic-model reviews, and 10 hard technical conflicts (seven year,
two power, and one displacement). Reason counts are not mutually exclusive.
This breakdown shows that simply retaining the qualifier would not safely clear
most of the remaining rows.

This is not an estimate for all 6.5 million vehicles. It is a diagnosis of the
frozen 20,000-row development cohort only.

## Citroën examples

| Source/candidate family | Support | Final v6 result | Interpretation |
|---|---:|---|---|
| `C3 PICASSO` | 11 | 10 resolved, 1 review-required | Raw-model recovery already works for most rows. A blanket normalization change would mostly duplicate current behavior. |
| `C4 PICASSO` | 17 | 3 provisional, 11 review-required, 3 hard conflicts | The qualifier is discovered, but other evidence gates still block acceptance. The traced `AAN163` row is a bodywork conflict (`AC/estate` versus TecDoc MPV), not a missing-model failure. |

The previously traced `T06023` row now resolves to KType `000033783` through
catalog-gated raw-model recovery. `AAN163` remains review-required with top
candidate KType `000059024` because bodywork conflicts. These examples show why
preserving or appending every suffix cannot be activated safely as a single
rule.

## What can safely be done next

1. Use the committed audit to rank only the 182 unresolved unique-family rows
   (172 review-required and 10 hard conflicts) by repeated manufacturer/family
   cohort.
2. Separate true model-family qualifiers such as `PICASSO` or `SPORTBACK` from
   generation labels, trim text, and token artifacts. `CORSA E`, `QASHQAI+2`,
   duplicated labels, and the shared Golf base remain explicitly review-only.
3. For each proposed exact source-model rule, require a unique catalog family
   plus year, fuel, engine/displacement/power, and bodywork/drive compatibility.
   A model rule must never override a hard conflict.
4. Reconcile Volvo Cross Country observations with the already reviewed Volvo
   context policy instead of creating competing mappings.
5. Activate only checksum-pinned reviewed rules, rerun the same 20,000 rows and
   the frozen 11,629-row holdout, and reconcile every terminal or KType change
   before any SCRUM-171 decision persistence or SCRUM-170 alias attachment.

## Reproduction

```bash
.venv/bin/python -m scripts.audit_model_qualifier_loss \
  --env-file /Users/kastriotrama/Documents/NorthStar/.env \
  --report outputs/scrum101-final-policy-v6-active-hns-20k-20260831.json \
  --output outputs/scrum101-model-qualifier-loss-v6-audit-20260831.json
```

The generated output is private local evidence and is intentionally not tracked.
It contains aggregate observations only: no plates and no VINs.
