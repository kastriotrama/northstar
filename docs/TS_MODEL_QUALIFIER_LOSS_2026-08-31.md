# Registry model qualifiers discarded during normalization

Date: 2026-08-31
Branch: `feature/SCRUM-101-integrated-matcher-validation`
Author: developer measurement, for review alongside PR #32

## Why this exists

The SCRUM-101 integration recorded nine lost resolutions on Citroën rows and
attributed them to "more specific family selection exposing bodywork
conflicts". Tracing those rows shows the exposure is real but the cause is one
layer earlier: normalization discards a model qualifier the registry supplied,
so the correct family survives only as a partial label, and the partial-model
guard then correctly refuses to resolve it.

This is not an argument against that guard. The guard is right. The rows should
never have reached it as partial matches.

## The mechanism

Rule `MOD-177` maps `C3 -> C3` scoped to Citroën. The raw registry model is
`C3 PICASSO`. The rule matches on the `C3` token, so `model_family` becomes
`C3` and `PICASSO` is dropped.

Traced on plate `T06023` (`northstar_ts_v323`, the 20,000-row local sample):

```
raw model "C3 PICASSO"  ->  model_family "C3"   (rule MOD-177)

1. C3 II (SC_)       conf=0.95  matched: model, year, fuels, displacement_cc
                                CONFLICT: bodywork   missing: power_kw
2. C3 PICASSO (SH_)  conf=0.80  matched: model, model_partial, year, fuels,
                                displacement_cc, power_kw, bodywork
```

Candidate 2 matches every available field with no conflicts and is the correct
car. It ranks second because token containment only lifts a partial label to
`candidate_threshold`, and it cannot resolve because it is marked
`model_partial`. Candidate 1, which conflicts on bodywork, ranks first.

`AAN163` is the same shape: raw `C4 PICASSO` normalizes to `C4`.

## Measurement

Source: `northstar_ts_v323`, 20,000 plate-stratified rows drawn from the
6,515,471-row local dump by sampling every 326th row across 22 of 23 plate
prefixes. Catalog: the promoted Neo4j graph, 55,808 KTypes. Read-only.

A row counts as losing a qualifier when the raw model's tokens are a strict
superset of the normalized `model_family` tokens. It is counted as recoverable
only when the catalog holds a more specific family whose tokens the registry
actually supplied.

| Measure | Rows | Share |
|---|---:|---:|
| Normalized rows carrying registry model text | 9,678 | — |
| Qualifier dropped | 2,727 | 28.2% |
| Catalog has the more specific family | 545 | 5.6% |

35 distinct registry-to-catalog mappings account for the 545.

## Candidate mappings, by observed support

These are observations, not proposals for activation. Each needs independent
review, and several are known to be wrong as stated (see below).

| Support | Manufacturer | Normalized | Catalog family present |
|---:|---|---|---|
| 91 | VOLVO | V60 | V60 CROSS COUNTRY |
| 83 | VOLVO | V90 | V90 CROSS COUNTRY |
| 68 | AUDI | A3 | A3 SPORTBACK |
| 33 | AUDI | A1 | A1 SPORTBACK |
| 32 | VOLVO | V40 | V40 CROSS COUNTRY |
| 30 | VW | Golf | GOLF SPORTSVAN |
| 26 | OPEL | Corsa | CORSA E |
| 24 | NISSAN | Qashqai | QASHQAI 2 |
| 19 | AUDI | A5 | A5 SPORTBACK |
| 18 | CITROËN | C4 | C4 PICASSO |
| 16 | TOYOTA | Yaris | YARIS CROSS |
| 16 | SEAT | Leon | LEON ST |
| 12 | CITROËN | C4 | C4 CACTUS |
| 10 | HONDA | Civic | CIVIC TOURER |
| 8 | AUDI | A3 | A3 LIMOUSINE |
| 7 | CITROËN | C5 | C5 AIRCROSS |
| 6 | TOYOTA | Verso | VERSO S |
| 5 | CITROËN | C3 | C3 PICASSO |
| 5 | CITROËN | C3 | C3 AIRCROSS |
| 5 | SEAT | Ibiza | IBIZA ST |
| 5 | OPEL | Mokka | MOKKA MOKKA X |
| 4 | SEAT | Leon | LEON SPORTSTOURER |
| 3 | AUDI | Q3 | Q3 SPORTBACK |
| 3 | TOYOTA | Aygo | AYGO X |
| 2 | CITROËN | C4 | C4 AIRCROSS |
| 2 | CITROËN | C4 | C4 SPACETOURER |
| 2 | VOLVO | S60 | S60 CROSS COUNTRY |
| 2 | TOYOTA | Corolla | COROLLA VERSO |
| 2 | CITROËN | C4 | C4 X |
| 1 | HYUNDAI | i20 | I20 ACTIVE |
| 1 | AUDI | Q5 | Q5 SPORTBACK |
| 1 | VW | Golf | GOLF VARIANT |
| 1 | AUDI | A5 | A5 AVANT |
| 1 | SKODA | Octavia | OCTAVIA COMBI |
| 1 | OPEL | Corsa | CORSA C |

## Known defects in this list

- `NISSAN Qashqai -> QASHQAI 2` is a token artifact. The registry text is
  `NISSAN QASHQAI+2`; `+2` normalizes to `2` and collides with the TecDoc
  generation label `QASHQAI 2`. This mapping is wrong as stated.
- `OPEL Corsa -> CORSA E` and `Corsa -> CORSA C` are generation letters, not
  trim qualifiers. Whether the registry text carries a generation or a trim
  needs a case-by-case ruling.
- `OPEL Mokka -> MOKKA MOKKA X` is a duplicated-token catalog label and should
  be checked against the source catalog rather than trusted.
- `VW Golf -> GOLF SPORTSVAN` (30) and `Golf -> GOLF VARIANT` (1) are distinct
  cars sharing a base token. A blanket Golf mapping must not be activated; this
  matches the existing critical-path instruction.

The Volvo Cross Country rows (91 + 83 + 32 + 2 = 208) very likely overlap the
47 Volvo proposals already in flight. This measurement should be reconciled
against that set rather than duplicated into competing drafts.

## Relationship to the recorded 24-case packet

The changed-resolved observations line up with this measurement:

- Three `i20 -> I20 ACTIVE` identity changes appear here with support 1 in this
  sample.
- Eight `Aygo -> AYGO X` identity changes appear here with support 3.
- The nine lost Citroën resolutions are `C4 PICASSO` (18) and `C3 PICASSO` (5).

Those changes are the same phenomenon observed from the matching side: with the
qualifier missing, the family is ambiguous, and any change to candidate
discovery moves rows between an ambiguous parent and a specific child.

## Limitations

- 20,000 rows is 0.3% of 6,515,471, and one plate prefix is absent. 5.6% is an
  indicator, not a national rate.
- The measurement asks only whether the catalog contains a more specific family
  the registry named. It does not establish that the specific family is the
  correct KType for any row.
- No mapping here is validated against an adjudicated decision. Support counts
  describe frequency in this sample, nothing more.
- The catalog used is the promoted 55,808-KType graph, not the 72,570-candidate
  audit set, so a family absent here may exist in the wider candidate pool.

## Suggested handling

1. Reconcile against the Volvo proposals already in flight before drafting
   anything new.
2. Treat generation letters (`CORSA E`, `CORSA C`, `QASHQAI 2`) separately from
   trim qualifiers (`CROSS COUNTRY`, `SPORTBACK`, `PICASSO`). They are
   different rulings.
3. Where approved, the correction belongs in the scoped `model_family` rules,
   not in matching. A recovered qualifier produces a full label, which resolves
   through the existing gates without weakening the partial-model guard.
4. Re-run the same pinned 20,000 rows after activation and reconcile every
   changed outcome, as required by the existing evidence contract.

## Reproduction

The measurement script is local and uncommitted. It reads
`staging.transportstyrelsen_raw` and the graph catalog, applies the active rule
set, and writes nothing. No plates appear in this document.
