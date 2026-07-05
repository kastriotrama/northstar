# Last Context

Keep the latest 10 task entries only.

## 2026-07-05

- Installed the official Atlassian Rovo MCP remote server in Codex config and completed `codex mcp login atlassian`.
- Corrected `.codex/config.toml` to use remote MCP URLs for Atlassian and GitHub instead of stdio placeholders.
- Retried Atlassian OAuth with narrower Jira-only scopes; login succeeded but live search still returns Jira-side 403 saying the app is not installed on the instance.
- Added a disabled `atlassian_api_token` MCP fallback using `ATLASSIAN_MCP_AUTHORIZATION`, documented in operations, for support/admin-directed testing without committing secrets.
- Validation: `codex mcp list`, `python3 -m ruff check .`, and `python3 -m pytest` pass.
- Remaining risk: Atlassian admin-side provisioning/entitlement is still needed; OAuth and permissions are enabled, but the server says the app is not installed on the instance.
- Jira Rovo direct JQL works against `https://northstarmasterdata.atlassian.net`; broad Rovo search may select another Atlassian instance.
- Moved `SCRUM-20`, `SCRUM-21`, and `SCRUM-22` to the review lane (`In Progress`) with ready-for-review comments.
- Added `infra/staging` Terraform skeleton for `SCRUM-23`, including region, environment, sizing, networking, and secret-reference variables.
- Installed Terraform 1.15.7 via the official HashiCorp Homebrew tap.
- Expanded staging IaC into provider-neutral Terraform module boundaries for PostgreSQL, Neo4j, Elasticsearch, and Redis.
- Validated acceptance criteria: `docker compose up -d` starts all four stores, all Docker health checks pass, `.env.example` has shared connection strings, and Terraform `fmt`, `init`, `validate`, and `plan -var-file=terraform.tfvars.example` pass.
- Added Jira workflow rule: create or switch to a `feature/<jira-key>-<short-slug>` branch before implementing a Jira story.

- Initialized project guidance files for Codex-driven development.
- Added backend pattern, mandatory testing, infrastructure, operations, and CI documentation expectations.
- Established MCP-first workflow for Jira and GitHub automation.
- Added FastAPI health scaffold with unit and integration tests.
- Next step: scaffold domain features after the health API, tests, Docker Compose, and CI are passing.
