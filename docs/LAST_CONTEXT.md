# Last Context

Keep the latest 10 task entries only.

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

## 2026-07-17

- Hardened PR #19 for `SCRUM-16`: COPY loads now validate source/written/landed counts before commit and roll back mismatches, while staging migrations verify exact columns, defaults, nullability, and primary keys; compile, Ruff, mypy, 135 runnable tests, and five live PostgreSQL tests passed, with 16 unrelated Neo4j tests skipped locally.

## 2026-07-16

- Hardened PR #18 for `SCRUM-15`: replaced delimiter-concatenated Alias identity with the shared versioned compact-JSON encoder, added collision/Unicode/input tests, verified duplicate IDs across all eight Neo4j labels, and made live migration checks compare labels, ordered properties, and schema types while excluding unrelated Neo4j built-in indexes; local compile, Ruff, mypy, and 107 runnable tests passed, with live Neo4j validation delegated to CI because Docker is stopped.

## 2026-07-15

- Implemented the `SCRUM-14` prefixed-ULID contract and dependency-free `northstar.node_ids` mint/parse/validate utility with all eight prefixes, injected time/entropy, package/CI/Docker inclusion, and 41 focused ID tests; full validation passed with 80 tests and six Neo4j skips, while Docker image validation remains pending because the daemon was stopped.
- Started `SCRUM-14` documentation work on `feature/SCRUM-14-opaque-id-generation`; added a stakeholder identity decision guide and agent rules covering plate-to-k-type reuse, lookup-before-mint, useful feedback boundaries, and future merge scope; documentation checks, Ruff, mypy, and 39 runnable tests passed.

## 2026-07-14

- Corrected PR #16's SCRUM-13 contract: reduced the catalog to seven canonical relationships, removed duplicate hierarchy/provenance edges, made Alias resolves candidate-safe, fixed all six traversals, and added Markdown contract plus live Neo4j tests; next step: merge after CI and review.
