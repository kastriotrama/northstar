# Last Context

Keep the latest 10 task entries only.

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

## 2026-08-05 — SCRUM-90

- Implemented Stage 1c on `feature/SCRUM-90-unit-conversion-structured-extraction`: pipeline v3 now extracts ISO registration/production dates and explicit ranges, structured engine code/family fields, canonical kW and displacement units, and retains reviewed multi-fuel/electrification behavior. Malformed, reversed, conflicting, or out-of-bound evidence routes to review; documentation and boundary/regression coverage were added. Ruff 0.16, strict mypy, and all 266 tests pass with healthy PostgreSQL and Neo4j. Jira, push, and PR remain unchanged.

## 2026-08-05 — SCRUM-89

- Published green stacked draft PRs #24/SCRUM-88 and #25/SCRUM-89. SCRUM-89 adds the `ts-translation-v2` dictionary boundary with reviewed transmission, fuel, electrification, and bodywork rules; accepted/proposed separation; exact-version loading; manufacturer/category scopes; conflict routing; persisted rule matches; stakeholder comment changes; documentation; and contract tests. Ruff 0.16, strict mypy, 216 local tests, datastore-backed CI, and image smoke tests pass. Jira remains unchanged.

## 2026-08-05 — SCRUM-88

- Implemented Stage 1a text canonicalization on `feature/SCRUM-88-text-canonicalization`: added an immutable raw/isolated working-record boundary, NFKC and whitespace normalization, field-aware code casing, safe typographic punctuation mapping, v2 pipeline integration, documentation, and Swedish/identifier regression tests; 208 tests pass, 43 datastore tests skip because Docker is stopped, and Ruff 0.16 plus strict mypy pass. Jira, push, and PR remain unchanged.

## 2026-08-05

- Published SCRUM-87 as draft PR #23 targeting `develop`; fixed four CI-only Ruff 0.16 findings while preserving sanitized job-boundary error handling, then verified the pipeline contract with Ruff 0.16, strict mypy, 193 local tests, image smoke tests, and the full datastore-backed GitHub Actions job. Jira was not changed; the PR is ready for manual review.

## 2026-08-04

- Fixed all Ruff 0.16 findings, pinned the CI lint version, and merged green PRs #21/SCRUM-18 and #22/SCRUM-19 into develop. Created `feature/SCRUM-87-pipeline-framework-record-contract` in an isolated worktree, reconciled the completed SCRUM-82 normalizer, and added an ordered transformer pipeline plus redaction-safe field-level decision traces, version-safe persistence, contract validation, documentation, and tests; Ruff and strict mypy pass, with 193 tests passing and 43 datastore tests skipped because Docker is stopped.
