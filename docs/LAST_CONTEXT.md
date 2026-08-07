# Last Context

Keep the latest 10 task entries only.

## 2026-08-07 — 20,000 passenger vehicles normalized with v5

- Deployed the CI-verified v5 normalizer to the isolated Hetzner NorthStar stack and selected exactly 20,000 Transportstyrelsen passenger records (`vehicle_type=PB`, EU category `M1`/`M1G`) from the source database. Imported them as immutable batch `normalization-passenger-ts20k-v5-20260807`: 20,000 processed, 2,702 resolved, 8,136 provisional, 9,162 review-required, and 0 failed. Exported a 20,000-row CSV containing selected TS evidence, complete raw JSON, normalized payload, rule IDs, review reasons, confidence, and version metadata. Older batches and VD-AI remain unchanged.

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

## 2026-08-07 — Charlie motorhomes permanently removed

- Permanently deleted all 49 confirmed motorhome/camper vehicles from every Charlie batch in the isolated test database. This removed 60 duplicated staging rows, 60 normalization results, and 38 review items; all three Charlie batches now contain the same 4,951 cars with no confirmed motorhomes and consistent completed job counts. The original public-safe Excel workbook remains unchanged and can restore the removed records if needed.

## 2026-08-07 — Charlie cars-only cohort

- Created `normalization-passenger-charlie-cars-4951-v5-20260807T0900Z` by excluding 38 records with official secondary code `SA` plus 11 clearly identified legacy camper records without that code. The cars-only cohort produced 662 resolved, 4,251 provisional, 38 review-required, and 0 failed; exported the complete review evidence to `northstar_charlie_cars_remaining_38_review_cases_2026-08-07.json`.

## 2026-08-07 — Charlie-plus 65-review JSON

- Exported all 65 review-required rows from `normalization-passenger-charlie-plus-5000-v5-20260807T0840Z` to `northstar_charlie_plus_remaining_65_review_cases_2026-08-07.json`, including complete public-safe raw evidence, normalized values, candidates, applied/candidate rule IDs, confidence, and blank decision fields. Reasons reconcile to 48 missing manufacturers, 6 unknown, 5 compound Brands, 2 converter/base gaps, and one each for corporate group, body code, motorhome supporting evidence, and transmission conflict; failed remains 0.

## 2026-08-07 — Charlie-plus pipeline-v5 import

- The original portable import correctly stopped because its expected results use the older workbook contract. Extracted exactly 5,000 public-safe TS raw records from `northstar_ts_normalization_charlie_plus_5000_2026-08-06.xlsx` and loaded them additively as `normalization-passenger-charlie-plus-5000-v5-20260807T0840Z` under active rule version `ts-review-20260807T081534112439Z`: 664 resolved, 4,271 provisional, 65 review-required, and 0 failed. The source workbook and prior immutable batches were not modified.
2026-08-07 — Reprocessed 20,000 passenger vehicles with active reviewed rules

- Fixed the CLI normalization job so it loads the latest activated translation and manufacturer-entity rules from PostgreSQL.
- Preserved the original base-rule batch and created `normalization-passenger-ts20k-v5-reviewed-20260807` from the same 20,000 passenger records.
- Reviewed batch result: 20,000 processed, 2,759 resolved, 16,992 provisional, 249 review-required, 0 failed.
- Remaining review reasons: 236 missing manufacturer comparisons, 6 evidence conflicts, 5 compound-brand reviews, 3 bodywork scope cases, 1 bodywork review, and 1 fuel conflict.
- Exported `northstar_ts_normalization_20k_v5_reviewed_2026-08-07.csv`; the original export remains unchanged for before/after comparison.
