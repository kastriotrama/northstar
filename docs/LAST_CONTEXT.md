# Last Context

Keep the latest 10 task entries only.

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

## 2026-07-30

- Implemented SCRUM-82 on `feature/SCRUM-82-ts-normalizer`: added versioned sanitized normalization results, accepted/candidate rule separation, deterministic review routing, retry-safe batch execution, explicit-batch CLI behavior, a fixed-width pilot reader, documentation, and tests; the isolated full suite passed with 229 tests, Ruff and strict mypy passed, and a real 1,000-vehicle pilot completed with zero technical failures and a verified no-op retry. The retained pilot requires manufacturer review because the legacy source lacks `Tillverkare`; Jira, push, and PR remain unchanged pending approval.

## 2026-07-29

- Published SCRUM-19 as draft stacked PR #22 targeting the SCRUM-18 branch/PR #21: added contract-verified `core.ingest_job_runs` migrations, retry-safe job/batch claiming, completed-batch no-op behavior, failure retry, balanced counts, sanitized error summaries, CLI migration support, documentation, and tests; Ruff, strict mypy, all 217 tests, four focused live PostgreSQL tests, and the real migration command passed, with retargeting to `develop` required after PR #21 merges.
- Published SCRUM-18 as draft PR #21 targeting `develop`, reran validation with 181 tests passing and 22 datastore tests skipped because PostgreSQL was unavailable, and moved SCRUM-18 plus directly completed subtasks SCRUM-60 through SCRUM-63 to In Progress with issue-specific Jira comments; manual review, CI completion, merge validation, and final Done transitions remain.

## 2026-07-28

- Implemented SCRUM-18 on `feature/SCRUM-18-review-queue`: added a contract-verified `core.review_queue` migration, idempotent enqueueing, status worklists, controlled resolution lifecycle, raw staging references, candidate evidence, normalization routing documentation, and sanitized manufacturer/hybrid examples.
- Validation passed with 203 tests, seven live PostgreSQL review-queue tests, Ruff, strict mypy, `git diff --check`, and the real `northstar-ingest migrate-review-queue` command; Jira comments and statuses remain unchanged.

## 2026-07-19

- Hardened PR #20 for `SCRUM-17`: added UUID-based retry idempotency, node-local linear correction constraints, complete schema-contract verification, corrected ledger documentation, and cross-store provenance rules in `AGENTS.md`; Ruff, mypy, 162 tests, and all 10 live PostgreSQL ledger tests passed, with 16 unrelated Neo4j tests skipped.
