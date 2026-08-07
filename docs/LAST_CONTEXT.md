# Last Context

Keep the latest 10 task entries only.

## 2026-08-07 — Web review queue for unresolved cars

- Added a Review Queue tab backed by the existing immutable `core.review_queue`: the latest 34,545-car batch correctly shows its 11 pending cases with TS evidence, current normalization, candidates, reasons, confidence, and Pending/In review/Resolved/Rejected controls.
- Review decisions require reviewer and reason. Vehicle-only decisions are audited in the queue; reusable decisions create an existing translation-rule or manufacturer-entity draft before resolution, then remain visible in reusable rule-change history for normal activation and reprocessing.
- Limited queue rule activity to current, unactivated translation-rule and manufacturer-entity drafts; activated rules no longer appear there.
- Added durable `review_draft` storage for in-review corrections. Starting or saving a review now preserves reviewer, field, canonical value, scope, rule/entity reference, and reason, and the form restores them when reopened. The one correction entered before this storage existed cannot be recovered and must be entered once more.
- Vehicle-only approvals now persist a new provenance-linked normalization result instead of only closing the queue item. Applied the existing review `1998` to record `990075154`: Manufacturer is now Jeep, `manufacturer_missing` is cleared, and the vehicle correctly moved from review-required 55% to provisional 80% because its AWD candidate remains unresolved; batch totals are now 4,866 resolved, 29,669 provisional, 10 review-required, and 0 failed.
- Ruff, strict mypy, JavaScript syntax, 28 focused tests, real local API reconciliation, and browser interaction checks pass with no console errors.

## 2026-08-07 — Vehicle dialog shows exact rule details

- Made applied and candidate rule badges in the vehicle detail dialog clickable. The selected translation rule, manufacturer entity, or built-in pipeline policy now expands directly inside the same dialog with its matching inputs, canonical output, scopes, status, and other relevant evidence. Code-based manufacturer policies use plain "If … then …" explanations, including the Tillverkare-missing Brand fallback and its supporting-evidence requirement.
- JavaScript syntax validation and the normalization-review integration suite pass (4 tests).

## 2026-08-07 — Latest reviewed rules installed in both databases

- Installed immutable target `ts-review-20260807T112656115381Z` in the local and isolated server PostgreSQL databases, advancing both from 67 to 229 active overrides.
- Verified target checksum `42a628f178321ef00eed3c2c4203a6c8264d13680ee816d0f209717e3d933a29` and applied a guarded 164-definition delta from the actual deployed baseline.
- The complete 35,000 PB workbook includes 362 official `SA` motorhomes and 93 legacy `body_code=08` motorhomes. After reproducing the collaborator-approved cars-only exclusion, reprocessed 34,545 retained cars locally and on the isolated live server as `normalization-passenger-34545-rules-229-cars-20260807`: 4,866 resolved, 29,668 provisional, 11 review-required, and 0 failed under pipeline v5.
- The 11 remaining reasons are 9 missing manufacturers, 1 compound Brand, and 1 fuel-carrier conflict.

## 2026-08-07 — Normalization decision review UX

- Replaced the narrow vehicle inspector with a focused two-column dialog showing TS source evidence, normalized output, field-specific missing/uncertain information, and raw-to-canonical mappings.
- Added a Decision guide documenting actual routing and fixed confidence levels: resolved 95%, provisional 80%, review-required 55%, and failed 0%.
- Exposed complete source evidence through the review API; visual browser verification found no errors and 337 relevant tests pass.

## 2026-08-07 — 35,000-passenger approved rules activated

- Activated immutable rule version `ts-review-20260807T112656115381Z` with 88 exact manufacturer definitions covering 182 approved review cases plus accepted passenger body code `98` → `other`. Reprocessed the cohort after excluding all 90 legacy `body_code=08` motorhomes: 34,545 cars produced 4,866 resolved, 29,668 provisional, 11 review-required, and 0 failed. Exported the 11-case JSON and updated the established `northstar_alpha_reviewed_rule_delta_2026-08-06.sql` artifact in place with 180 cumulative changes from its retained baseline to the 229-override target; Ruff, strict mypy, all 345 runnable tests, JSON validation, live PostgreSQL reconciliation, and byte-identical SQL regeneration pass.

## 2026-08-07 — Reviewed 35,000-passenger workbook re-import

- Imported `northstar_ts_normalization_passenger_35000_reviewed_2026-08-07.xlsx` into fresh additive batch `normalization-passenger-35000-reviewed-cars-current-20260807T1130Z` using active rule version `ts-review-20260807T091238765007Z`. Excluded 365 confirmed motorhomes and normalized 34,635 cars: 4,825 resolved, 29,614 provisional, 196 review-required, and 0 failed. Exported all review evidence to `northstar_passenger_35000_remaining_review_cases_2026-08-07.json`; JSON validation and live PostgreSQL reconciliation pass with no retained `SA` motorhomes.

## 2026-08-07 — 20,000 passenger vehicles normalized with v5

- Deployed the CI-verified v5 normalizer to the isolated Hetzner NorthStar stack and selected exactly 20,000 Transportstyrelsen passenger records (`vehicle_type=PB`, EU category `M1`/`M1G`) from the source database. The base import produced 2,702 resolved, 8,136 provisional, 9,162 review-required, and 0 failed; reprocessing the same records with the active PostgreSQL rules created `normalization-passenger-ts20k-v5-reviewed-20260807` with 2,759 resolved, 16,992 provisional, 249 review-required, and 0 failed. Exported the reviewed 20,000-row CSV; older batches and VD-AI remain unchanged.

## 2026-08-07 — Three-workbook motorhome exclusion re-import

- Re-imported Atlas, Borealis, and Charlie-plus into fresh additive cars-only batches using active rule version `ts-review-20260807T091238765007Z`, excluding records identified as motorhomes by primary or secondary `SA` body code plus the established camper-brand evidence list. Atlas excluded 92 and retained 4,908 cars with 16 review-required; Borealis excluded 80 and retained 4,920 with 4 review-required; Charlie-plus excluded 86 and retained 4,914 with 1 review-required. All three imports completed with 0 failures; older immutable batches and source workbooks remain unchanged.

## 2026-08-07 — Atlas 17-case decisions reviewed

- Checked the proposed Atlas decisions against all raw evidence without changing rules or data. Confirmed `MILLER-MARYLAND` is a motorhome via primary `body_code=SA` and should be excluded; 12 car manufacturer decisions are safe as exact/evidence-gated rules, while `STEFANS FORD ROADSTER`, `BG-HOT`, `BOSSE`, and `SUPER SNAKE` should remain manual. The specific historical `JAGUAR DAIMLER SOU` → Daimler decision is supportable only as a narrow year/evidence rule, not a global Jaguar/Daimler mapping.

## 2026-08-07 — Atlas 17-review JSON

- Exported all 17 review-required rows from `normalization-passenger-atlas-cars-latest-20260807T1010Z` to `northstar_atlas_remaining_17_review_cases_2026-08-07.json`, including complete public-safe raw evidence, normalized values, candidates, applied/candidate rule IDs, confidence, and blank structured decision fields. Reasons reconcile to 15 missing manufacturers, one unknown legal manufacturer, and one Jaguar/Daimler compound Brand; failed remains 0 and JSON validation passes.
