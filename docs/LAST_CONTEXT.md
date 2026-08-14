# Last Context

Keep the latest 10 task entries only.

## 2026-08-14 — Production normalization gaps documented

- Added `docs/NORMALIZATION_PRODUCTION_GAPS.md` as a concise handoff of completed normalization assets and remaining production work. It identifies the background worker, resumable checkpoints, incremental reprocessing, deployment ordering, manually triggered GitHub Action, and operational visibility as the remaining implementation, while preserving the 2,138 ambiguous records for restricted/manual review. No pipeline, database, or workflow behavior changed.

## 2026-08-13 — V3.2.2 agreed-brand batch activated

- Activated immutable version `ts-review-20260813T104653142376Z` with 138 non-shadowing exact Brand rules covering 191 agreed workbook records, plus code-level corroborated motorhome-fab routing and expanded strong self-built/replica text. A full 25,295-car audit found zero canonical conflicts and zero resolved/provisional regressions. Reprocessed as `normalization-remote-passenger-cars-only-v322-20260813`: 10,475 resolved, 12,682 provisional, 2,138 review-required, 0 failed, reducing review by another 215. Updated the established SQL delta in place to 654 cumulative definitions/875 overrides; 88 focused tests and Ruff pass. The remaining queue is dominated by 2,094 manufacturer-missing records, mostly ambiguous custom/one-off identities.

## 2026-08-13 — Remaining 2,353 review solution

- Mapped all 2,353 V3.2.1 review rows back to the workbook. The next defensible automatic batch is roughly 180–210 records: 167 guarded manufacturer rows whose referenced legacy/specialist/fab rules were absent from the workbook candidate sheet, 16 row/exact manufacturer decisions, six strong Special Modified rows, and a small set of explicit technical corrections; supported subsets of 23 motorhome proposals can also route out of passenger scope after evidence checks. The remaining bulk is intentionally ambiguous: 704 low-volume custom builders, 671 generic customs, 21 non-specific makes, and 704 manual-tail records spanning 695 brands (687 singleton brands). These should be restricted/manual unless stronger T12, registered-builder, VIN, or authoritative make evidence is obtained.

## 2026-08-13 — V3.2.1 guarded rules activated and reprocessed

- Activated immutable version `ts-review-20260813T094159016933Z` with 250 exact/evidence-guarded V3.2.1 manufacturer rules after excluding three lookup-key collisions and confirming zero canonical conflicts or status regressions across 25,295 cars. Added supported motorhome/test terminal routing and strong EGET/EGEN TILL Special Modified handling, then reprocessed as `normalization-remote-passenger-cars-only-v321-20260813`: 10,291 resolved, 12,651 provisional, 2,353 review-required, and 0 failed (525 fewer reviews). Updated the established SQL delta in place to 516 cumulative definitions/737 overrides; 86 focused tests and Ruff pass. Remaining review is dominated by 2,301 manufacturer-missing records; generic/custom identities remain deliberately unresolved and restricted.

## 2026-08-13 — V3.2.1 review-workbook audit

- Reviewed `northstar_review_required_2878_analysis_v321.xlsx` without changing the workbook or database. It reconciles all 2,878 unique review records and usefully classifies 2,167 rows, leaving 711 manual, but it is not a complete executable import artifact: 267 rule IDs appear in decisions while only 152 candidates are defined, 123 referenced IDs are absent from the candidate sheet, 8 candidates are unused, 81 candidates lack confidence/current-row metadata, and 143 lack sources. Recommend accepting the scoped high-confidence decisions in staged dry runs while keeping the 1,383 generic/custom-builder rows restricted and unresolved unless stronger T12/builder evidence exists.

## 2026-08-13 — Current review-required JSON export

- Exported all 2,878 review-required cars from active batch `normalization-remote-passenger-cars-only-v31-safe-20260812` to `northstar_review_required_2878_2026-08-13.json`. Every record includes plate/VIN, core TS fields, complete raw evidence, current normalized output, candidates, applied/candidate rules, review reasons, and a blank `manual_decision` structure for stakeholder answers. JSON parsing, exact record count, required structures, and the 2,880 reason assignments (two records carry multiple reasons) were verified.

## 2026-08-12 — V3.1 safe rules and strict contracts

- Added strict V3.1 ruleset and test-file JSON Schemas with raw/normalized namespaces, enumerated stages/operators/actions/fields, conditional action requirements, and non-empty constraints, plus a dependency-free semantic validator for rule IDs, stage/priority ambiguity, dictionaries, regexes, canonical fields, and terminal ordering. Activated immutable version `ts-review-20260812T150129143531Z` with scoped corroborated BUIK, Tiger Avon, DMC DeLorean, Factory Five Roadster, and Nilsson special-purpose restrictions; generic HOT ROD now stays unresolved with restricted parts/review guidance, and SA + class II routes outside the passenger dataset. Reprocessed 25,295 cars: 9,746 resolved, 12,671 provisional, 2,878 review-required, and 0 failed, reducing review by 17. All 110 focused tests, Ruff, JSON parsing, PostgreSQL examples, and SQL export pass; Bertone Ritmo behavior remains unchanged pending PM confirmation.

## 2026-08-12 — V3 schema and regression-test review

- Reviewed the proposed v3 JSON Schema and 15 behavioral tests without changing code or the database. Eight tests already match the active normalized behavior; seven expose useful gaps: Nilsson special-purpose parts restriction, guarded Bertone Ritmo policy, Buick typo corroboration, generic custom-identity routing, motorhome exclusion routing, DeLorean aliasing, and restricted Tiger/Factory Five kit-car handling. The schema is a useful envelope but needs enumerated operators/actions/fields, operation-specific required properties, non-empty action/match constraints, and a separate schema for tests before it can serve as an executable contract. Bertone Ritmo remains the only stakeholder policy decision; the other gaps can be implemented with scoped rules.

## 2026-08-12 — Verified-v2 guarded manufacturer rules activated

- Implemented executable regex, strict-year, fab-code corroboration, custom-text exclusion, and manufacturer-conflict guards for the reviewed v2 proposal. A 5,529-row dry run identified the expected 2,698 affected rows (2,682 manufacturer resolutions plus 16 TEST quarantines), with four same-canonical BINZ overlaps consolidated to zero overlaps and zero canonical conflicts. Activated immutable version `ts-review-20260812T143204290357Z` with 134 guarded manufacturer definitions and official TS fuel mappings (`19=biodiesel`, `F=flex_fuel`), then reprocessed 25,295 cars: 9,729 resolved, 12,671 provisional, 2,895 review-required, and 0 failed. Real PostgreSQL checks confirm TEST quarantine, strict historic/fab mappings, and Toyota diesel+biodiesel flex-fuel; 106 focused tests and Ruff pass, and the established SQL delta now has 262 cumulative changes/483 overrides.

## 2026-08-12 — Manufacturer-missing review export

- Exported all 5,529 `manufacturer_missing` rows from the active 25,295-car cohort to `northstar_manufacturer_missing_5529_2026-08-12.csv`. The UTF-8 CSV contains 42 analysis columns, including plate/VIN, Brand and model evidence, base manufacturer, vehicle/body/fuel fields, candidates, review reasons, applied rules, and the complete raw JSON. Database count and artifact-tool structure checks both pass.
