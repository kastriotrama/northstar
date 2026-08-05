# Last Context

Keep the latest 10 task entries only.

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

## 2026-08-05 — SCRUM-94

- Implemented the sanitized golden normalization/reconciliation corpus on `feature/SCRUM-94-golden-test-corpus`: added 205 versioned cases (165 TS normalization and 40 candidate reconciliation), complete approved output/evidence snapshots, sensitive-field and corpus-shape gates, explicit approval, aggregate per-case unified regression diffs, a CI verification step, documentation, and tests. Applied the latest stakeholder bodywork comments in replay-safe `ts-translation-v4`: European `estate` replaces stored `wagon`, BA remains `truck`, goods van remains `van`, passenger marketing becomes `passenger_van`, and official AF remains `multi_purpose_vehicle`; versions 2 and 3 remain replayable. Ruff 0.16, strict mypy, the standalone corpus verifier, and all 322 tests pass. Jira, push, and PR remain unchanged.

## 2026-08-05 — SCRUM-93

- Implemented the final Phase 1 confidence/routing gate on `feature/SCRUM-93-confidence-routing`: added versioned weighted composite scoring, exact resolved/provisional/review boundaries, hard-conflict/non-exact/phonetic/ambiguity overrides, complete decision traces and alternatives, immutable catalog/policy-versioned PostgreSQL persistence, source validation, database constraints, and the `migrate-confidence-routing` command. Graph documentation now consistently uses the `0.70 <= confidence < 0.90` provisional band. Ruff 0.16, strict mypy, all 313 tests, and the real migration command pass with PostgreSQL and Neo4j. Jira, push, and PR remain unchanged.

## 2026-08-05 — SCRUM-92

- Implemented Stage 2b on `feature/SCRUM-92-phonetic-matching`: added versioned, deterministic phonetic recovery for allow-listed manufacturer/model text; review-only phonetic manufacturer scoping and model evidence; configurable combined scoring; exact algorithm provenance; and hard exclusions for VIN, plate, KType, engine/type codes and alphanumeric identifiers. Phonetics cannot independently create or automatically accept a match and cannot bypass hard conflicts. Ruff 0.16, strict mypy, and all 294 tests pass with PostgreSQL and Neo4j. Jira, push, and PR remain unchanged.

## 2026-08-05 — SCRUM-91

- Implemented Stage 2a on `feature/SCRUM-91-fuzzy-matching`: added immutable manufacturer/alias candidate indexes, deterministic Damerau-Levenshtein and token scoring, year/fuel/engine context evidence, numeric model-series protection, configurable thresholds/margins, review-queue payloads, and safe exact/fuzzy/global scope gates. Fuzzy/global, conflicting, weak, and ambiguous matches cannot become automatically eligible. Ruff 0.16, strict mypy, and all 281 tests pass with PostgreSQL and Neo4j. Jira, push, and PR remain unchanged.
