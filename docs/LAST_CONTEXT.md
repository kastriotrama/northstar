# Last Context

Keep the latest 10 task entries only.

## 2026-07-07

- Started `SCRUM-10` on `feature/SCRUM-10-fastapi-resolve-api-july-7` from `origin/develop`.
- Scaffolded the FastAPI resolve API module with `router.py`, `service.py`, `schemas.py`, `/resolve/status`, and stub `POST /resolve`.
- Added unit and integration tests proving resolve routes work and OpenAPI includes the resolve endpoints.

## 2026-07-06

- Completed `SCRUM-25` with log-level and batch-size validation, source-path helpers, and JSON log extra fields.
- Completed `SCRUM-26` with per-datastore `ping()` wrappers and aggregate ingestion datastore healthcheck coverage.
- Completed `SCRUM-27` with CLI stubs for `healthcheck`, `load`, `normalize`, `graph-write`, `index`, `tecdoc`, and `transportstyrelsen`, plus `--batch-id` logging metadata.
- Added plain-language Jira completion comments for `SCRUM-25`, `SCRUM-26`, and `SCRUM-27`.

## 2026-07-05

- Started `SCRUM-9` on `feature/SCRUM-9-python-ingestion-service-skeleton`.
- Added Python ingestion package scaffold with config, structured JSON logging, datastore client wrappers, and `northstar-ingest` stub CLI commands for TecDoc and Transportstyrelsen.
- Added unit tests for ingestion config, logging, datastore wrapper construction, and CLI command registration.
- Corrected Jira workflow handling: only `SCRUM-24` should be treated as started for this request; untouched sibling subtasks were returned to To Do.
- Added agent rule to avoid transitioning or commenting on sibling Jira subtasks unless they were explicitly started or directly worked.
- Added agent rule requiring simple plain-language Jira completion comments covering what changed, why, verification, and remaining risk or next step.
- Jira Rovo direct JQL works against `https://northstarmasterdata.atlassian.net`; broad Rovo search may select another Atlassian instance.
- Added Jira workflow rule: create or switch to a `feature/<jira-key>-<short-slug>` branch before implementing a Jira story.
