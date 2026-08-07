# Last Context

Keep the latest 10 task entries only.

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

## 2026-08-07 — Three latest-rule cars-only imports

- Re-imported the Atlas, Borealis, and Charlie-plus 5,000-row public-safe workbooks into fresh additive cars-only batches using active rule version `ts-review-20260807T091238765007Z`. After excluding confirmed motorhomes (39 Atlas, 41 Borealis, 50 Charlie), Atlas produced 675 resolved, 4,269 provisional, 17 review-required, and 0 failed across 4,961 cars; Borealis produced 695/4,260/4/0 across 4,959 cars; Charlie produced 713/4,236/1/0 across 4,950 cars. Remaining cases are manufacturer-only and no confirmed motorhomes remain in the selected batches.

## 2026-08-07 — Reviewed-rule SQL artifacts refreshed

- Regenerated both reviewed-rule SQL artifacts directly from immutable PostgreSQL versions. `northstar_latest_reviewed_rule_delta.sql` contains 75 changes after deployed baseline `ts-review-20260806T170328350936Z`; `northstar_alpha_reviewed_rule_delta_2026-08-06.sql` now contains only the 91 additions/changes after its former target `ts-review-20260806T133621914615Z`. Both reach target `ts-review-20260807T091238765007Z`, reconstruct all 140 overrides, embed checksum `4e1f262da545a7c37928396cd3a96bae6821c794c5d1dd584df09005498f0d96`, regenerate byte-identically, and pass `git diff --check`.

## 2026-08-07 — Charlie cars review reduced to one

- Activated 28 reviewed manufacturer entities as immutable version `ts-review-20260807T091238765007Z` and implemented evidence-aware fixes for Ferrari California bodywork, Porsche PDK transmission, primary police code `93`, self-built vehicles, compact BMW models, DS/PSA context, shared Citroën/DS evidence, and duplicated manufacturer prefixes in models. Reprocessed all 4,951 retained cars into `normalization-passenger-charlie-cars-4951-rules-20260807T0920Z`: 713 resolved, 4,237 provisional, 1 review-required (`JAGUAR DAIMLER`), and 0 failed. All 343 tests, Ruff, strict production mypy, and JSON validation pass.
