# Last Context

Keep the latest 10 task entries only.

## 2026-07-17

- Hardened PR #19 for `SCRUM-16`: COPY loads now validate source/written/landed counts before commit and roll back mismatches, while staging migrations verify exact columns, defaults, nullability, and primary keys; compile, Ruff, mypy, 135 runnable tests, and five live PostgreSQL tests passed, with 16 unrelated Neo4j tests skipped locally.

## 2026-07-16

- Hardened PR #18 for `SCRUM-15`: replaced delimiter-concatenated Alias identity with the shared versioned compact-JSON encoder, added collision/Unicode/input tests, verified duplicate IDs across all eight Neo4j labels, and made live migration checks compare labels, ordered properties, and schema types while excluding unrelated Neo4j built-in indexes; local compile, Ruff, mypy, and 107 runnable tests passed, with live Neo4j validation delegated to CI because Docker is stopped.

## 2026-07-15

- Implemented the `SCRUM-14` prefixed-ULID contract and dependency-free `northstar.node_ids` mint/parse/validate utility with all eight prefixes, injected time/entropy, package/CI/Docker inclusion, and 41 focused ID tests; full validation passed with 80 tests and six Neo4j skips, while Docker image validation remains pending because the daemon was stopped.
- Started `SCRUM-14` documentation work on `feature/SCRUM-14-opaque-id-generation`; added a stakeholder identity decision guide and agent rules covering plate-to-k-type reuse, lookup-before-mint, useful feedback boundaries, and future merge scope; documentation checks, Ruff, mypy, and 39 runnable tests passed.

## 2026-07-14

- Corrected PR #16's SCRUM-13 contract: reduced the catalog to seven canonical relationships, removed duplicate hierarchy/provenance edges, made Alias resolves candidate-safe, fixed all six traversals, and added Markdown contract plus live Neo4j tests; next step: merge after CI and review.
- Finalized the SCRUM-12 schema review fixes: separated manual record/assertion identity, replaced mutable-content hashes with persisted provenance or supersession, added explicit single-live-target `REFERS_TO` examples, and hardened recursive repository-bound Markdown link validation; next step: merge the stacked fix into PR #11 after CI and review.

## 2026-07-09

- Fixed CI workflow gaps on `feature/SCRUM-11-ci-pipeline-foundation`: added `develop` to push triggers (default branch previously never ran CI post-merge), concurrency cancellation, pip caching, and fixed the datastore health retry loop to use `run_health_checks` so a raising client retries instead of crashing.
- Completed the Docker image build subtask (parent `SCRUM-11`): added multi-stage `Dockerfile` (`api` and `ingestion` targets, non-root user, dependency-layer caching, env-driven `API_HOST`/`API_PORT` with exec-form PID 1 signal handling), `.dockerignore`, and a CI `images` job that builds and smoke-tests both images on PRs with no publish step.
- Validated live: both images build, API image serves `/health` ok against Docker Compose datastores, `API_PORT=9000` override works, graceful shutdown in 1s; pytest 33 passed, ruff and mypy clean.
- Pinned `elasticsearch<9` in `pyproject.toml` after the 9.x client silently failed ping against the 8.14 server; client majors should match server majors.
- Added `docs/PHASE_1_PLAN.md` capturing the Phase 1 roadmap (epics 1-10) with a repo status mapping.
