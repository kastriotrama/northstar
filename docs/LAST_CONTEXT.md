# Last Context

Keep the latest 10 task entries only.

## 2026-07-05

- Started `SCRUM-9` on `feature/SCRUM-9-python-ingestion-service-skeleton`.
- Added Python ingestion package scaffold with config, structured JSON logging, datastore client wrappers, and `northstar-ingest` stub CLI commands for TecDoc and Transportstyrelsen.
- Added unit tests for ingestion config, logging, datastore wrapper construction, and CLI command registration.
- Corrected Jira workflow handling: only `SCRUM-24` should be treated as started for this request; untouched sibling subtasks were returned to To Do.
- Added agent rule to avoid transitioning or commenting on sibling Jira subtasks unless they were explicitly started or directly worked.
- Added agent rule requiring simple plain-language Jira completion comments covering what changed, why, verification, and remaining risk or next step.
- Jira Rovo direct JQL works against `https://northstarmasterdata.atlassian.net`; broad Rovo search may select another Atlassian instance.
- Moved `SCRUM-20`, `SCRUM-21`, and `SCRUM-22` to the review lane (`In Progress`) with ready-for-review comments.
- Added `infra/staging` Terraform skeleton for `SCRUM-23`, including region, environment, sizing, networking, and secret-reference variables.
- Expanded staging IaC into provider-neutral Terraform module boundaries for PostgreSQL, Neo4j, Elasticsearch, and Redis.
- Validated acceptance criteria: `docker compose up -d` starts all four stores, all Docker health checks pass, `.env.example` has shared connection strings, and Terraform `fmt`, `init`, `validate`, and `plan -var-file=terraform.tfvars.example` pass.
- Added Jira workflow rule: create or switch to a `feature/<jira-key>-<short-slug>` branch before implementing a Jira story.
