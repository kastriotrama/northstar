# Last Context

Keep the latest 10 task entries only.

## 2026-08-06 — Three portable 1,000-car cohorts

- Added three mutually exclusive 1,000-passenger-car Excel bundles (`alpha`, `bravo`, and `charlie`) from distinct database batches. Every workbook contains sanitized TS source rows, its exact 1,000 normalized results, the active `ts-review-20260805T184254528647Z` rule version, all translation/manufacturer catalogs, and active overrides. All three imported together into a clean PostgreSQL database and reproduced 3,000/3,000 results with `verified=true`; ZIP integrity, synthetic plate/VIN checks, source/workbook non-overlap checks, and visual review pass.

## 2026-08-06 — Portable normalization test bundle

- Exported and visually verified `outputs/019fadda-d238-75d3-8312-142dfdce2612/northstar_normalization_test_bundle_2026-08-06.xlsx`: 250 sanitized TS staging records with deterministic synthetic plate/VIN values, 250 expected normalized results, 99 effective translation rules, the complete 184-row built-in manufacturer catalog, the 189-row effective runtime manufacturer view, 18 active database overrides, exact immutable rule-version metadata, and explicit Adria/Dethleffs exclusions. Added `import-normalization-bundle` to validate the workbook, populate an isolated PostgreSQL database, normalize, and compare every generated result; clean-database import and retry both reproduced 37 resolved, 212 provisional, 1 review-required, and 0 failed with `verified=true`. Validation: 371 tests, Ruff, strict mypy, compilation, XLSX archive checks, 205 golden cases, and a 500-identifier leak scan all pass.

## 2026-08-05 — General manufacturer and converter rules

- Activated immutable version `ts-review-20260805T184254528647Z` with complete-prefix rules that tolerate punctuation and accents for Škoda legal entities and PSA/Citroën, while retaining token boundaries and PSA child allow-listing. Added Knaus/FCA, Fiat/Adria, and Fiat/Dethleffs converter handling that keeps Fiat as manufacturer and preserves the converter. Re-imported 250 passenger cars: 37 resolved, 212 provisional, 1 review-required, and 0 failed; only Jaguar/Daimler remains protected. Peugeot/PSA model-evidence behavior is regression-tested and unchanged. Jira, push, and PR remain unchanged.

## 2026-08-05 — Manufacturer entity hierarchy

- Grouped 13 redundant exact Brand entities beneath the approved SAAB, VW, Mazda, and Škoda prefix parents while preserving each exact value as reviewed source evidence. Exact reviewed examples now resolve through the parent rule; unseen prefix matches remain provisional. Activated immutable version `ts-review-20260805T183018213844Z` and re-imported 250 passenger cars: 37 resolved, 207 provisional, 6 review-required, and 0 failed. Focused tests, lint, type checks, live API checks, and PostgreSQL provenance checks pass; Jira, push, and PR remain unchanged.

## 2026-08-05 — Brand search and manufacturer audit details

- Extended the normalization review workspace so vehicle search includes the sanitized TS Brand field, rows and the selected-car inspector show Brand, and rule provenance distinguishes applied from candidate rules. Manufacturer entity selection now shows Created at and Updated at from immutable activation history; draft creation time is persisted, while built-in unversioned entries are labeled honestly. Re-imported the 250 passenger cars so Brand-prefix examples expose both `MFR-BRAND-PREFIX-FALLBACK` and their reviewed entity rule. Ruff, strict mypy, all 363 tests, live API search, PostgreSQL lifecycle checks, and browser verification of Saab Brand search/entity timestamps pass; Jira, push, and PR remain unchanged.

## 2026-08-05 — Approved Brand-prefix manufacturer fallback

- Added and directly activated DB policy `MFR-BRAND-PREFIX-FALLBACK` plus 13 reviewed passenger-car Brand aliases in immutable version `ts-review-20260805T180257125170Z`. When Tillverkare is missing, complete Brand-prefix matches now produce a provisional manufacturer; unsafe substrings and compound `ADRIA`, `DETHLEFFS`, or `DAIMLER` terms remain in review. Re-import moved 95 cars out of review: 37 resolved, 207 provisional, 6 review-required, and 0 failed. The six remaining cases are the three protected compounds, Knaus/FCA, Škoda legal entity, and PSA/Citroën. Focused tests and live PostgreSQL checks pass; Jira, push, and PR remain unchanged.

## 2026-08-05 — Approved Model/Variant manufacturer fallback

- Added and directly activated immutable DB policy `MFR-MODEL-VARIANT-FALLBACK` in version `ts-review-20260805T175608808573Z`. When Tillverkare and Brand are both absent, the normalizer can use a reviewed Manufacturer entity alias at the start of Model or Variant as a provisional manufacturer requiring later corroboration. Complete-token/prefix matching prevents substring guesses, populated Brand is never overridden, and conflicting Model/Variant manufacturers remain in review. Ruff, strict mypy, all 358 tests, DB verification, and a 250-passenger-car re-import pass. The current totals intentionally stayed unchanged because all 101 review cases have Brand populated; no Jira update, push, or PR was made.

## 2026-08-05 — Passenger-car-only review cohort

- Restricted deterministic TS pilot selection and rule-workbench re-imports to passenger cars: explicit EU category must be `M1`/`M1G`, with `PB` fallback only when category is absent. Rebuilt `normalization-review-passenger-250-v1` from the full source with 243 M1 and 7 M1G records, all vehicle type PB: 36 resolved, 113 provisional, 101 review-required, and 0 failed. No trucks, buses, trailers, motorcycles, tractors, other categories, or bodywork/category conflicts remain in this cohort. Focused tests and live PostgreSQL evidence checks pass; Jira, push, and PR remain unchanged.

## 2026-08-05 — Legacy Brand manufacturer resolution

- Added 103 exact canonical legacy Brand rules covering all 106 records that had no Tillverkare, spanning passenger vehicles, motorcycles, tractors, trailers, buses, and historical brands. Exact keys avoid unsafe substring matches and are visible as active manufacturer entities. Re-import mapped all 106 to a manufacturer, eliminated `manufacturer_missing` (106 → 0), moved 35 vehicles out of review, and reduced review-required from 162 to 127 with no failures; 71 corrected vehicles remain review-required only for overlapping bodywork/category conflicts. Ruff, strict mypy, all 353 tests, PostgreSQL evidence queries, and browser verification pass. Jira, push, and PR remain unchanged.

## 2026-08-05 — Manufacturer entities and review backlog

- Extended the Rules workspace with 239 reviewed/discovered Tillverkare entities from the latest batch. Reviewers can draft vehicle-manufacturer, bodybuilder/converter, corporate-group, or unknown classifications; converter rules use Tillverkare grundfordonet and retain the converter; Brand-only entities remain review-only until explicitly approved. Activated entity rules join the same immutable cumulative version and safe re-import flow. Added live review-reason guidance for the overlapping 162 cases: 106 missing Tillverkare, 55 Brand evidence gaps, and 85 category/bodywork conflicts. Ruff, strict mypy, JavaScript syntax, all 352 tests, live PostgreSQL reads, and browser draft/discard validation pass with zero drafts left behind; Jira, push, and PR remain unchanged.
