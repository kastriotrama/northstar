# TS → TecDoc immediate repair package — 2026-08-28

Next package completed: [scoped family recovery and readiness](TS_TECDOC_SCOPED_RECOVERY_2026-08-28.md).

Scope: SCRUM-101 / SCRUM-172–175, local integrated worktree on
`feature/SCRUM-101-integrated-matcher-validation`. No commit, push, deployment,
source re-import, decision-ledger write, alias activation or Neo4j write.

## Implemented extraction correction

The catalog contains Saab `9-3` and `9-5` family aliases, but punctuation
normalization turns them into `9 3` and `9 5`. Their two-character compact
length failed the generic recovery minimum. Recovery now accepts these two
explicit hyphenated names in `brand`/`model` only when the manufacturer is Saab
and the catalog aliases belong to canonical models starting with that family.

- Returns a family query, never selects a generation or KType itself.
- Does not interpret decimal numbers, approval/variant/version identifiers,
  `93`/`95`, unrelated manufacturers or shared trim aliases as this evidence.
- Preserves explicit model precedence, source contradictions and every existing
  technical, partial-name, context, margin and candidate-only gate.
- Version: `shared-family-query-recovery-v2-saab-hyphenated`.
- No new manufacturer, engine-fingerprint or bodywork semantic rules activated.

## Same-input 20,000-row replay

The replay compares against `outputs/scrum101-coverage-repair-20k-20260828.json`,
not the earlier pre-repair 1,510-resolution run. Source, catalog and reviewed
rules are checksummed and must match before execution. Results are written
with exclusive creation and owner-only permissions; earlier evidence is retained.

The complete read-only replay finished in 597.3 seconds:

| Outcome | Before | After | Change |
|---|---:|---:|---:|
| Resolved | 2,220 | 2,284 | +64 |
| Provisional | 2,104 | 2,218 | +114 |
| Review-required | 13,967 | 13,788 | −179 |
| Hard conflict | 1,596 | 1,597 | +1 |
| Policy excluded | 112 | 112 | 0 |
| Normalization review | 1 | 1 | 0 |
| Total | 20,000 | 20,000 | 0 |

Zero failures, zero lost resolutions, and zero changed KTypes among the 2,220
previously resolved records. Missing-model cases fell **4,785 → 4,312**.
All 473 recovered missing models came from raw brand evidence: 64 resolved,
114 provisional, 294 still review-required, and one power conflict. The 294
reviews split into 229 bodywork and 65 candidate-margin cases. All 114 new
provisional cases meet resolved scoring but remain candidate-only/not graph-safe.
There are also 44 reason-only provenance changes for explicit model recovery;
their terminal outcomes and KTypes are unchanged.

All 64 gained resolutions were individually replayed without divergence and
audited against the recorded gates. They are Saab 9-3s: 56 estate and eight
other 9-3 family entries. All have five matching technical fields (year, fuel,
displacement, power, bodywork); none has an observed TS engine code. These are
**matcher-resolved**, not independently confirmed vehicle identities.
No 9-5 resolution gain is claimed.

Artifacts:

- Full report: `outputs/scrum101-saab-recovery-20k-20260828.json`.
- Private raw/normalized/candidate evidence: `outputs/scrum101-saab-recovery-review-20260828.json`.
- Pending independent-review checklist: `outputs/scrum101-saab-recovery-audit-64-20260828.json`.

Report SHA-256: `7ff9dcac327527989b68cc2dc10c072310443f16a0c99b12704d0271d300f390`.
Matcher source digest: `9154160eabcfff788c19ee8a6374598f70055cb25e7ffabbf26898dbb7534f19`.

Pinned inputs:

- Source: `normalization-vdai-passenger-full-v323-20260817-part-`, first 20,000 rows.
- Catalog: `tecdoc-0326-all-active-candidates-v5-fuel-local`, 72,570 candidates.
- Rules: `ts-engine-fingerprint-heldout-v1-disabled-20260824`.
- Normalization: `normalization-pipeline-v6`; fuel alignment: `unpinned-legacy`.
- Context policy: `context-comparison-v1`, zero activated compatibility rules.

## Full targeted bodywork evidence

All 549 rows in the three exact ranked groups were replayed. The diagnostic
uses the actual scorer and catalog, and exposes family siblings even if they
fall below the candidate cutoff or outside the returned top five. It is not an
alternative ranker and does not accept any proposed replacement.

| Exact group | Rows | Review | Hard conflict | Finding |
|---|---:|---:|---:|---|
| Volvo XC40, raw model XC40 / AC | 216 | 204 | 12 | No estate sibling in the scoped XC40 catalog family; 12 power conflicts |
| Volvo XC60 II, raw model XC60 / AC | 190 | 177 | 13 | No estate sibling in the scoped XC60 catalog family; 7 power and 6 year conflicts; one row also has a drive disagreement |
| Golf VII, raw brand `VOLKSWAGEN, VW`, model GOLF / AC | 143 | 143 | 0 | Estate siblings exist for every row, but best estate labels are partial model matches |

For the 143 Golf cases:

- 140 best-estate candidates reach the candidate threshold.
- 138 best-estate candidates have no reported conflicts; absence of a conflict
  is **not** proof of a correct match.
- 137 cases already include an estate in at least one returned top-five list.
- All 143 best-estate labels are partial matches; five have a power conflict,
  and 21 have missing/non-confirming fuel evidence in the scorer.
- Example technical signature: 2019, petrol, 1,498 cc, 110 kW, TS estate. The
  hatchback KType `000126840` scores 1.05 (unclamped separation score), while
  estate `000126844` scores 0.88. The hatchback gets exact `GOLF` model text;
  the estate matches `GOLF VARIANT` partially/phonetic despite matching bodywork
  and the available technical fields. Both remain subject to the existing gates.

Conclusion: Golf is not a missing-catalog import problem in this group. The
next proposal should use reviewed **source-context-scoped family evidence**
to retrieve the estate family, then compare all siblings with unchanged
technical and ambiguity gates. Do not add a global `GOLF`→`GOLF VARIANT` alias
or declare estate and hatchback equivalent. Volvo needs scoped vocabulary
adjudication; its 381 reviews are not 381 guaranteed future resolutions.

Private evidence: `outputs/scrum101-bodywork-sibling-review-549-v2-20260828.json`.
The initial sibling packet is retained. V2 includes actual TS `kw`/`ccm` and
alternative measurement inputs, not just the normalized field names. Plate
numbers stay in ignored, owner-readable local artifacts, not Jira comments.

## Audit of the preceding repair's 736 changed-resolution cases

`scripts/audit_match_repair_packet.py` audits existing replay evidence and
creates a pending review checklist; it does not invent independent verdicts.

- 716 gained resolutions, six lost resolutions, 14 changed resolved KTypes.
- The 730 accepted cases have supporting resolved attempts with exact
  manufacturer scope and no accepted conflict, partial-model or phonetic flag.
- All 730 have observed year/fuel evidence; 675 have displacement, 730 power,
  728 bodywork and 90 drive observations. **None has a directly observed TS
  engine code.** Catalog engine allocations are not independent engine evidence.
- Confirmed technical-field counts in those attempts: 603 cases have five,
  76 have six, 47 have four, three have three, and one has two. These counts
  are diagnostic, not a new acceptance threshold or proof of correctness.
- Priority review: **24 cases** = six losses + 14 changed identities + four
  newly resolved sparse-evidence cases. All 736 still require independent review.
- Five losses are Audi A3 Sportback generation/margin ambiguities. One selects
  a candidate-only Sportback KType and remains provisional. The 14 changed
  identities select specific A1/A3 Sportback catalog entries instead of generic
  A1/A3 entries, consistent with explicit raw model text but not independently
  approved as ground truth.

Private checklist: `outputs/scrum101-coverage-repair-audit-736-20260828.json`.
Full original evidence: `outputs/scrum101-coverage-repair-review-20260828.json`.
This is a same-cohort technical audit, not an independent held-out validation.

## Verification and remaining gates

Relevant unit tests cover positive/negative numeric recovery, manufacturer and
catalog scope, identifier-field rejection, explicit model priority, ambiguous
generations, year conflicts, exact bodywork group selection, below-threshold
siblings, audit gate failures, and private measurement evidence.

Validation: **670 unit tests**, **205 golden cases**, repository Ruff,
strict mypy (116 source files), and `git diff --check` passed. Actual local
PostgreSQL source/catalog reads, the full 20k replay, 549 bodywork case replays
and all 64 new resolved replays completed. No new dependency was added. Graph
mutation/reconciliation integration tests were not run because no graph or
cross-store write path changed; graph promotion is not claimed as validated.

Reproduce from the integrated worktree with the existing environment (use a
new output filename; existing evidence cannot be overwritten):

```sh
/tmp/northstar-review-20260828/.venv/bin/python scripts/validate_local_matcher_cohort.py \
  --code-root /Users/kastriotrama/Documents/NorthStar-SCRUM-101-integrated \
  --env-file /Users/kastriotrama/Documents/NorthStar/.env \
  --source-prefix normalization-vdai-passenger-full-v323-20260817-part- \
  --catalog-version tecdoc-0326-all-active-candidates-v5-fuel-local \
  --rule-version ts-engine-fingerprint-heldout-v1-disabled-20260824 \
  --expected-candidates 72570 --limit 20000 \
  --baseline-report outputs/scrum101-coverage-repair-20k-20260828.json \
  --output outputs/scrum101-saab-recovery-20k-repeat.json
```

Before any accepted-rule activation or fleet rollout:

1. Independently adjudicate the 24 priority cases and the remaining changed
   accepted identities; keep unsure verdicts explicit.
2. Review the Volvo source-context compatibility proposals and Golf family
   retrieval proposal separately. Do not change the margin/technical gates.
3. Freeze a separate grouped/stratified held-out population before tuning.
4. Validate approved, versioned rules and report gained/lost/changed KTypes.
5. Use SCRUM-164/165 prerequisites → SCRUM-171 ledger → SCRUM-170 controlled
   promotion/alias attachment and PostgreSQL/Neo4j reconciliation.

Progress/evidence comments were added to directly worked SCRUM-172–175.
No Jira story is marked Done by this package; independent semantic approval
and held-out acceptance criteria remain open.
