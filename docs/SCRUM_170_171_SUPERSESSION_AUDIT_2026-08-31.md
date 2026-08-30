# SCRUM-170/171 decision supersession and graph reconciliation

Date: 2026-08-31

## Outcome

The current PostgreSQL decision heads and Neo4j aliases are internally
consistent with the historical policy that created them, but they are not safe
to roll forward unchanged under the current matcher policy.

The read-only replay evaluated all 9,122 current Transportstyrelsen decision
heads against:

- candidate catalog `tecdoc-0326-all-active-candidates-v6-multifuel-complete-local`;
- normalization rules `ts-engine-fingerprint-heldout-v1-disabled-20260824`;
- reviewed context policy `volvo-bodywork-reviewed-v1-20260830`; and
- matcher code digest
  `fe252a5b5972959e06ba10aa54a3a4a09b1ce8ffb39af7d7627c2d7a149fbb6b`.

No PostgreSQL decision, decision head, alias, KType, or Neo4j relationship was
written by either audit.

## Decision replay

| Current-policy terminal | Decisions | Meaning |
| --- | ---: | --- |
| Resolved | 4,472 | Same KType as the historical decision |
| Review required | 4,533 | Must not remain or become a live alias |
| Provisional | 116 | Candidate is not graph-safe |
| Hard conflict | 1 | Year conflict; must not be promoted |
| **Total** | **9,122** | Fully accounted |

There are 356 changed top candidates, but every changed candidate is now
non-resolved: 239 review-required, 116 provisional, and one hard conflict. No
current-policy resolved decision changes KType identity.

The main reason population is 4,483 bodywork conflicts and 139 drive-type
conflicts. These are policy gates, not permission to erase or weaken evidence.
The mixed-fuel v6 catalog is still awaiting independent domain adjudication,
so this replay is an audit result rather than an approved persistence run.

## Neo4j reconciliation

The graph contains 1,000 current Transportstyrelsen aliases across 105 promoted
KTypes.

| Alias state under current policy | Aliases |
| --- | ---: |
| Fresh resolved alias, same KType | 467 |
| Stale: now review-required | 518 |
| Stale: now provisional | 15 |
| Unknown decision or divergent target | 0 |
| **Total** | **1,000** |

The 467 fresh aliases cover 70 of the 105 promoted variants. The other 35
variants are supported only by stale aliases. A retirement must therefore
restore `:Provisional` when it removes the final active match assertion for a
variant. There are also 4,005 current-policy resolved decisions that have no
graph alias yet.

## Implemented safety controls

The SCRUM-170 graph writer now:

- prevents a second active decision assertion for the same source alias, even
  when both decisions point to the same KType;
- requires explicit decision supersession before a replacement alias can be
  attached;
- retires the active `REFERS_TO` edge without deleting the immutable alias;
- preserves the historical target with `PREVIOUSLY_REFERRED_TO` plus successor
  decision and reason evidence;
- is idempotent only when the same successor decision and retirement reason are
  replayed; and
- restores `:Provisional` and clears live promotion properties when no other
  active match-decision alias supports the KType.

These controls were exercised only against disposable Neo4j integration-test
fixtures. The existing 1,000 local aliases were not changed.

## Required activation sequence

1. Independently approve or reject the v6 mixed-fuel change packet and freeze
   the final catalog/policy pins.
2. Run the frozen, previously unscored holdout. Reject activation on new hard
   conflicts, unsupported identity changes, or degraded reviewed cohorts.
3. Re-evaluate the 9,122 heads with the approved pins and persist successor
   decisions through SCRUM-171. Historical rows remain immutable and each old
   head receives one linear supersession edge.
4. Retire all 1,000 old graph assertions after their successor decisions are
   current. For the 467 still-resolved vehicles, attach successor aliases to
   the same KTypes. Leave the 533 non-resolved vehicles without a live alias.
5. Attach the other 4,005 resolved successor aliases in controlled batches,
   with collision and KType-cardinality preflight on every batch.
6. Reconcile every PostgreSQL current head against Neo4j. The target state is
   4,472 active aliases, zero aliases for non-resolved heads, zero multi-target
   aliases, zero unknown decisions, and zero target mismatches.

Local private evidence:

- `outputs/scrum171-persisted-heads-v6-volvo-audit-20260831.json`
- `outputs/scrum170-graph-alias-freshness-audit-20260831.json`

The first report contains internal source record identifiers but no plates or
VINs. Neither report should be published as a public PR artifact.
