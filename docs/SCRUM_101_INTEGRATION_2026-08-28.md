# SCRUM-101 local integration and controlled comparison

## Scope and code

Worktree: `/Users/kastriotrama/Documents/NorthStar-SCRUM-101-integrated`.
Branch: `feature/SCRUM-101-integrated-matcher-validation`, based on `23a2ca2`.
Changes remain local and uncommitted; some imported patches are staged by the three-way application. Nothing was pushed or deployed.

Combined the latest developer commits with the matcher/provenance/engine-set/candidate-only changes from `NorthStar-SCRUM-95-98` and the isolated sampling/weight-manifest fixes from `NorthStar-PR31-review-20260827`. Both source worktrees' implementation files were preserved.

## Corrections implemented

- Partial model labels discover candidates but cannot resolve automatically merely through token containment. No arbitrary two-technical-field acceptance rule was introduced. Stronger partial-family acceptance remains a future independently validated policy.
- Explicit raw model recovery precedes longer brand labels; conflicting recognized source models require review. Catalog-recognized specific raw models are not allowed to lose to broader normalized names solely because the latter scores higher.
- Manufacturer mapping precedes recovery; exact catalog-scoped short alphanumeric names such as A4 are recoverable.
- Only a trailing numeric-dot chassis suffix such as `(205.487)` is excluded from commercial-series comparison. Genuine C 43/C 63 conflicts remain blocked; arbitrary parenthesized model information is not stripped.
- Bodywork ranking remains at its previous weight until independently calibrated. No estate-to-SUV rule was activated.
- Matching entrypoints consume pinned, source-scoped fuel alignments. Unknown/unsealed/unsupported sets fail closed; compatibility is directional and neutral, not confirmed fuel evidence. Legacy comparisons retain existing behavior.
- Activated vocabulary rows reject updates, deletions, additions and truncation. Versions are sealed atomically by the seed operation; identical seed retries are no-ops. This is tested on isolated PostgreSQL, not activated in the source database.
- Reviewed fuel corrections refresh comparison tokens. Normalization output is versioned as `normalization-pipeline-v6`, so it does not reuse the v5 normalization identity. Historic v5 workbook contracts fail explicitly under the new runtime; the existing fixture is preserved and tests generate a metadata-retagged temporary copy for current-version import verification. Existing PostgreSQL snapshots were not modified.
- Existing reviewed engine-set handling, engine provenance and candidate-only graph-safety restrictions remain intact. No fingerprints were activated.

## Same-record comparison

Both runs read the same first 20,000 retained TS rows under prefix `normalization-vdai-passenger-full-v323-20260817-part-`, using 72,570 candidates from `tecdoc-0326-all-active-candidates-v5-fuel-local` and rule version `ts-engine-fingerprint-heldout-v1-disabled-20260824`. Source, candidate and rule-set content checksums agree. All database access was read-only; audit progress was stdout only.

Alignment pin: `unpinned-legacy`. The new database alignment loader was validated with synthetic isolated tests, not activated or applied to this fleet cohort. The new developer hybrid-token behavior is part of the integrated code comparison; it is not a claim that every normalized payload is byte-identical.

| Terminal | Existing local baseline | Integrated |
|---|---:|---:|
| Resolved | 1,515 | 1,510 |
| Provisional | 1,506 | 1,551 |
| Review-required | 15,668 | 15,519 |
| Hard conflict | 1,198 | 1,307 |
| Policy excluded | 112 | 112 |
| Normalization review | 1 | 1 |
| Failed / unmatched | 0 / 0 | 0 / 0 |
| Total | 20,000 | 20,000 |

The baseline exactly reproduces the earlier recorded 20k run. Review volume fell by 149, but hard conflicts rose by 109 and resolved count fell by five: this is not proof of improved acceptance coverage.

There are 1,177 records with a changed terminal or top candidate. Four became resolved, nine lost resolved status, and eleven stayed resolved with a different KType. Those 24 cases form the priority review packet, not a representative calibration sample.

The eleven changed resolved identities follow more specific TS text: eight ordinary Aygo candidates changed to Aygo X and three i20 candidates changed to i20 Active. The nine lost resolutions are Citroën cases (one C3 Picasso, three C4 X, five C4 Picasso) where more specific family selection exposes bodywork conflicts. These observations support targeted review; they are not independently approved ground truth.

Diagnostic occurrences (overlapping, not additive): missing model 8,028→7,905; bodywork conflict 6,000→5,762; below-margin route 798→789; candidate-only graph-unsafe 1,315→1,338; model-series conflict 16→55. Recovery can expose additional conflicts, so declining missing-model counts do not imply accepted matches.

The v6 metadata bump and reviewed-fuel refresh were finalized after the long comparison started. A final-code scan of all 20k found zero reviewed-record-policy rows and zero affected fuel-token changes; all 24 changed-resolved cases were replayed under the final code with the same terminal and KType. No persistence was performed with the intermediate metadata.

## Evidence artifacts (local, private)

- `outputs/scrum101-baseline-20k-20260828.json`
- `outputs/scrum101-integrated-20k-20260828.json`
- `outputs/scrum101-comparison-20k-20260828.json`
- `outputs/scrum101-changed-resolved-review-24-v2-20260828.json`

Reports are created with mode 0600 and refuse overwrites. The v2 review packet supersedes the first draft by including raw variant/model-number/type-text/bodywork detail. It contains plates and must remain private and outside Git. Each case has pending status, no invented verdict, raw TS evidence, normalization/rule information, previous candidate details, current candidate attempts, alternatives and routing traces. Candidate engine allocations are not observed TS engine evidence.

## Validation and remaining gate

Final verification: 572 unit tests plus 37 isolated PostgreSQL integration tests; 205 golden cases; Ruff; strict mypy on 114 files; JavaScript syntax; git diff whitespace checks. Tests cover the safety regressions, comparison accounting, private artifact writing, calibration integration, ledger/run schemas, vocabulary immutability and normalization bundle identity. Full Neo4j/Redis/Elasticsearch integration and deployment image builds were not rerun.

Next: domain-review the 24 changed-resolved cases, investigate the increased model-series/conflict cohorts, and prepare a fresh evidence-complete calibration population with independent held-out evaluation. Preserve the existing 200 labels diagnostically; do not invent population weights or acceptance labels. Do not launch a new full-source run, policy activation, decision persistence or graph promotion based only on these counts.

Jira scope worked: SCRUM-101, SCRUM-148 and SCRUM-149. Status transitions and production changes remain unapproved. SCRUM-164/165 → SCRUM-171 → SCRUM-170 dependencies remain in force; this task does not complete those stories.
