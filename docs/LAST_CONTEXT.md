# Last Context

Keep the latest 10 task entries only.

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

## 2026-08-05 — Translation rule review workbench

- Added a Rules tab to the normalization review screen with dictionary search/filtering, immutable source-match evidence, vocabulary-constrained output editing, draft notes, version activation, and safe re-import of the selected 1–1,000-record batch into a new staging/result batch. Activated rule versions are immutable and cumulative, drafts never affect normalization, re-import is blocked until drafts are approved or discarded, and the UI shows before/after routing totals. Added API/service/repository/reprocessing boundaries, migrations, documentation, and regression coverage. Ruff, strict mypy, JavaScript syntax, all 349 tests, and the real browser workflow against PostgreSQL pass; a no-op BDY-110 verification re-import preserved 7 resolved, 81 provisional, 162 review-required, and 0 failed. Jira, push, and PR remain unchanged.

## 2026-08-05 — Manufacturer and hybrid evidence resolution

- Added `normalization-pipeline-v4` manufacturer evidence gates: Brand requires an agreeing allow-listed model, VIN WMI, or TecDoc KType manufacturer; conflicts and unknown populated `Tillverkare` remain review-only. Added reviewed legal-entity aliases, evidence-gated MINI/PSA/FCA marketed-brand handling, and KABE base-manufacturer retention. Explicit ELHYBRID/LADDHYBRID now preserves petrol/diesel and adds electricity after evaluating all three TS fuel fields. Reprocessing the same 250 records moved 67 from review to provisional and 7 to resolved (236 → 162 review-required), eliminated all ten fuel conflicts, and produced no failures or regressions. Ruff, strict mypy, the 205-case golden corpus, and all 341 tests pass; Jira, push, and PR remain unchanged.

## 2026-08-05 — Normalization review dashboard

- Built a read-only operations dashboard and filtered API for the latest normalization batch, with 250 real Transportstyrelsen samples, status totals, manufacturer/model/engine search, status/manufacturer/bodywork/fuel/transmission filters, pagination, and a sanitized evidence inspector showing normalized fields, confidence, review reasons, decision trace, and rule IDs. The current sample has 14 provisional and 236 review-required records with zero technical failures. Service, API, repository, privacy, JavaScript syntax, and browser interaction checks pass; Ruff, strict mypy, and all 328 tests pass. Jira, push, and PR remain unchanged.
