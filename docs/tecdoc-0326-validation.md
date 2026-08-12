# TecDoc 0326 vehicle hierarchy validation

| Field | Value |
|---|---:|
| Source release | `0326` |
| Data format | `2.70` |
| Source Table 120 rows | 72,600 |
| Active Table 120 KTypes extracted | 72,570 |
| Deleted Table 120 KTypes excluded | 30 |
| Manufacturers used | 584 |
| Model series used | 11,462 |
| Distinct engines referenced | 17,173 |
| Table 125 applicability rows retained | 139,822 |
| KTypes without a Table 125 engine link | 14,843 |
| KTypes with multiple distinct engines | 8,868 |
| Maximum distinct engines on one KType | 13 |
| Links to a deleted-but-present Table 155 engine | 1 |
| KTypes missing production start | 0 |

## Validation result

The supplied fixed-width tables are sufficient to extract the stakeholder-
confirmed core hierarchy:

```text
100 Manufacturer -> 110 Model series -> 120 KType passenger car
                                      -> 125 engine allocation -> 155 Engine
```

All active extracted KTypes have valid Table 100 and Table 110 ancestors. All
Table 125 engine IDs used by active KTypes resolve to a physical Table 155 row.
One referenced engine is marked deleted in Table 155; it is retained as source
evidence and must not become an active canonical engine without review.

Table 125 contains country and date applicability rows. Repeated rows for the
same `(KType, Engine)` are therefore grouped as one engine allocation with many
applicability records, not interpreted as hundreds of different engines.

## First 1,000-KType gate

- 1,000 unique active KTypes extracted.
- 784 had one distinct engine.
- 181 had two distinct engines.
- 30 had three distinct engines.
- 5 had four distinct engines.
- Every manufacturer, model, KType, engine and source-row reference resolved.

## Remaining canonical-load decisions

- Table 120 fuel, drive, transmission and body fields currently retain their
  exact TecDoc key-table codes. Table `052` is still required to attach the
  official labels before mapping those codes to NorthStar vocabulary.
- Table `020` is still required to formally name language IDs. Validation used
  language `004`, for which all required hierarchy descriptions were present.
- Platform remains optional until Tables `714` and `715` are supplied.
- The canonical persistence layer must write KType-to-engine relationships
  separately from vehicle nodes so multiple engines and country applicability
  are not flattened or lost.

## Relationship persistence validation

The first 1,000 real KTypes were persisted locally as a versioned PostgreSQL
candidate batch:

| Field | Value |
|---|---:|
| KTypes processed | 1,000 |
| Distinct KType-engine candidates | 1,256 |
| KTypes with multiple engine candidates | 216 |
| Duplicate writes on identical rerun | 0 |

Each candidate retains the Table 100/110/120 source references, Table 155
engine row, deleted-engine flag, and every Table 125 country/date applicability
row. Candidates remain outside Neo4j until one engine is resolved or the KType
is deliberately split into multiple canonical variants.

The Neo4j promotion writer was validated against the real local graph service:
an identical rerun retains one `USES_ENGINE` edge, while promotion of a second
engine to the same canonical variant aborts transactionally and leaves the
existing edge unchanged.

## Controlled canonical promotion

Official English labels from Tables 020/030/052 were used to interpret Table
155 fuel key 088. Across all 72,570 active passenger-car KTypes, 40,965 meet
the current automatic-promotion gates when complete-source Table 120
displacement consensus is allowed. The remaining records are held back by
14,843 missing engines, 8,867 ambiguous engines, 4,012 unsupported or
unresolved fuel values, and 3,883 unresolved displacements.

A controlled local run promoted 1,000 eligible KTypes and wrote 2,737 new
PostgreSQL candidates: 1,000 aliases, 1,000 provisional vehicle variants, 507
engines, 204 model families and 26 manufacturers. Neo4j contains 1,000
provisional variants and 1,000 `USES_ENGINE` relationships from this run; an
identical graph rerun matched the same 1,000 records without duplication.

Platform Tables 714/715 are not present. Consequently, ModelFamily and
Manufacturer nodes can be created, but a VehicleVariant cannot yet traverse
to them through the accepted `VehicleVariant -> Platform -> ModelFamily ->
Manufacturer` hierarchy. The PostgreSQL vehicle candidate preserves both
source keys with `hierarchy_link_status=awaiting_platform_mapping`; no
unsupported direct relationship is created in Neo4j.

The controlled gate was subsequently run over the complete 72,570-KType
passenger source as batch `tecdoc-0326-canonical-full-local`. It promoted
40,965 KTypes in 82 rollback-safe Neo4j transactions and persisted 101,178
PostgreSQL candidates: 40,965 aliases, 40,965 provisional vehicle variants,
10,971 shared engines, 7,973 model families and 304 manufacturers. PostgreSQL
and Neo4j reconcile at 40,965 variants and 10,971 engines. An identical full
rerun wrote zero additional PostgreSQL candidates and matched the same 40,965
graph rows.

## Corrected Table 120 coverage

The official format documentation defines Table 120 as the authoritative KType
vehicle record with mandatory power, fuel and engine-type facts. Table 125 only
allocates an optional reusable Table 155 engine number. The earlier
`engine_missing` gate therefore excluded valid KTypes too aggressively.

Corrected batch `tecdoc-0326-canonical-full-v2-local` promotes 55,808 KTypes:
40,965 have a Table 155 Engine and `USES_ENGINE`; 14,843 have no Table 125
allocation and are represented as provisional VehicleVariants using Table 120
facts only. Those variants carry `engine_link_status=allocation_missing`, raw
TecDoc fuel/engine-type codes, power and technical displacement. They have no
fabricated Engine node or relationship. PostgreSQL and Neo4j both reconcile to
these exact coverage counts.
