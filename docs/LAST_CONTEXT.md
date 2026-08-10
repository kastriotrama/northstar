# Last Context

Keep the latest 10 task entries only.

## 2026-08-07 — Remaining 11,134 model candidates analyzed

- All 11,134 remaining model candidates have a resolved manufacturer; 11,133 are provisional only because model family is unapproved, while one also has the existing fuel-combination conflict.
- Found 1,128 distinct manufacturer/model pairs: 150 pairs occurring at least 20 times cover 7,580 cars, 256 pairs occurring at least 10 times cover 9,049, and 385 pairs occurring at least five times cover 9,895. There are 444 one-off pairs.
- Split the work into 7,024 single-token candidates, 4,110 composite candidates, at least 2,144 with explicit trim/powertrain/body suffixes, and 420 short-code candidates requiring special caution. Recommended direct manufacturer-scoped rules for clean families, reviewed decomposition rules for composites, and retaining internal codes such as Kia SL/ED until corroborated.

## 2026-08-07 — First 25 model-family rules implemented locally

- Added immutable `ts-translation-v6` with 25 manufacturer-scoped, complete-prefix model-family rules for the highest-volume TS candidates; suffix text remains source evidence, wrong-manufacturer and partial-token matches remain candidates, and no TecDoc inference is used.
- Added the Model Family category to the Rules UI and guarded SQL catalog activation `northstar_model_family_rule_catalog_v6.sql`, preserving all 229 active overrides.
- Reprocessed 34,545 passenger cars locally as `normalization-passenger-34545-model-v6-local-20260807`: 12,494 resolved, 22,040 provisional, 11 review-required, and 0 failed. The rules accepted 8,229 model families and moved 7,116 vehicles from provisional to resolved; 11,134 model candidates and 15,182 missing-model cases remain.
- Local web now serves v6 with 129 translation rules including 25 Model Family rules. JavaScript syntax, Ruff, strict mypy, the golden corpus, and 403 tests pass; the one pre-existing stateful bundle-fixture collision remains excluded.

## 2026-08-07 — Reviewed drive rules added as catalog v5

- Added accepted `drive_type=awd` rules for the official TS `is_4wd=1` flag and manufacturer-scoped 4MATIC, xDrive, quattro, and 4Motion terms; `is_4wd=0` deliberately remains unresolved and never guesses FWD or RWD.
- Advanced the immutable application catalog to `ts-translation-v5`, retained v4 for exact replay, exposed Drive rules and plain explanations in the Rules/vehicle UI, and added a guarded SQL activation artifact that preserves the live 229 overrides.
- Activated v5 locally and reprocessed all 34,545 passenger cars into immutable batch `normalization-passenger-34545-drive-v5-local-20260807`: 5,378 resolved, 29,156 provisional, 11 review-required, and 0 failed. AWD is now accepted on 7,656 cars (7,655 TS flags plus one marketing-only match); all 985 scoped marketing matches were applied with no new drive review reasons. Compared by source order, 512 provisional cars became resolved. The previously reviewed Jeep returned to review because its vehicle-only correction is not yet replayed across a copied batch.
- Restarted the local app from the updated worktree after confirming the previous processes had cached v4. The local Rules API now exposes `ts-translation-v5`, 104 total rules, and accepted Drive rules DRV-001/002/003/004/008.
- JavaScript syntax, targeted Ruff, strict mypy, and 401 tests pass. One stateful bundle-import integration test remains blocked by a pre-existing fixture batch in the shared local PostgreSQL test database. The SQL must be applied only with the matching application version; the live environment has not yet been changed by this task.

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

## 2026-08-10 — TS special-vehicle safety evidence implemented locally

- Exposed registration plate, source batch, and real-versus-synthetic identity in review results and made plate searchable. Preserved the TS inspection-tail description and every body-code position as immutable source evidence.
- Added official T12 and special-purpose body-code meanings, grouped amateur/rebuilt vehicles as `Special Modified`, excluded them from TecDoc/parts matching, and routed other special-purpose vehicles to manual parts review. Description-only `AMATÖR` records retain candidate codes without claiming an exact code.
- Verified source line 440113 as real plate AYZ946 and exercised it end-to-end. Added a portable 97-passenger-car XLSX containing source evidence, expected results, all 231 v7 translation rules, 184 base manufacturers, and the active 229 overrides; all 97 results reproduce exactly.
- Ruff, JavaScript syntax, 76 focused tests, the application bundle validator, formula/error scans, and visual checks of all 10 workbook sheets pass. Existing database batches have not yet been mutated or reprocessed.

## 2026-08-10 — Complete 496,251-car portable bundle aligned to special policy

- Split the complete local Transportstyrelsen passenger batch into 100 importable XLSX workbooks: 99 parts of 5,000 and one part of 1,251, with 496,251 unique source IDs and no owner fields.
- Retained lossless raw TS and normalized payload JSON plus the translation catalog, manufacturer entities, overrides, rule metadata, and known issues in every workbook.
- Refreshed 2,940 special-purpose-body-code vehicles and 94 amateur-built passenger records under `TS-SPECIAL-VEHICLE-V1`; final totals are 269,803 resolved, 223,367 provisional, 3,081 review-required, and 0 failed.
- Added guarded, idempotent SQL activation `northstar_special_vehicle_policy_v1.sql` targeting immutable version `ts-review-20260810T143500000000Z`, and taught all active-rule loading paths to replay that policy from SQL.
- Verified 75 focused tests and Ruff, all 100 workbook contracts/JSON payloads, representative first/middle/last visual renders, and rollback-only SQL validation. The local database remains on the prior immutable version until explicitly activated.
