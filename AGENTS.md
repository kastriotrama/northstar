# AGENTS.md

## Codex Operating Rules

- Execute-first: implement changes immediately unless the user explicitly asks for a plan only.
- Follow-up continuity: keep prior context, constraints, and style across follow-up messages unless the user says "reset context".
- Question gate: ask questions only for missing credentials, destructive or irreversible actions, or true ambiguity that can change outcomes.
- Single-question policy: when blocked, ask exactly one concise question and continue after the answer.
- Assumption policy: if details are missing, choose conservative defaults and state assumptions briefly in the final response.
- Dependency policy: do not add new dependencies unless necessary; if needed, install and continue, then report what was added.
- Verification policy: run relevant tests, lint, type checks, or builds after edits; if not possible, state exactly what could not be verified.
- Edit scope policy: touch only files required for the task; avoid unrelated refactors.
- Git safety: never run destructive git commands unless explicitly requested by the user.
- Retry policy: on command failure, retry once with a safer or compatible variant before escalating.
- Performance policy: prefer fast repo tools such as `rg`, targeted reads, and minimal scans.

## Context Memory

- Keep a short rolling log in `docs/LAST_CONTEXT.md`.
- After each completed task, append one brief entry with date, what changed, validation, and remaining risk or next step.
- Keep entries concise.
- Keep only the latest 10 entries.

## Backend Pattern

- FastAPI app entrypoint stays thin.
- `api/main.py` is entrypoint-only.
- App bootstrap lives in `api/app/main.py`.
- New backend work goes in `api/app/features/<feature>/`.
- Each feature should use `router.py`, `service.py`, `repository.py`, and `schemas.py` where applicable.
- Routers own HTTP concerns: request parsing, response models, status codes, and HTTP exceptions.
- Services own orchestration and business rules.
- Repositories own database access.
- Integrations own external API/provider communication.
- Shared modules should contain pure helpers only.
- Settings and environment reads stay in `api/app/core/settings.py`.
- Do not read environment variables directly from feature code.
- Do not raise FastAPI `HTTPException` outside routers.
- Use constructor-injected services, repositories, and adapters.
- Keep sync code paths fully sync and async code paths fully async.

## Testing Policy

- Every new feature, service, repository, integration adapter, and utility must include tests in the same change.
- Tests are mandatory unless the user explicitly says to skip them.
- If tests cannot be added, explain why and document the remaining risk.
- Prefer focused unit tests for business logic and adapters.
- Add integration tests when behavior depends on databases, queues, external services, or app startup.
- Do not consider backend work complete until relevant tests pass.
- Do not add untested business logic.
- Bug fixes must include a regression test when practical.

## Infrastructure Rules

- Local infrastructure must use Docker Compose first.
- Required local services: PostgreSQL, Neo4j, Elasticsearch, and Redis.
- Every datastore must have a health check.
- Application services must wait for healthy datastore dependencies.
- Do not hardcode credentials, ports, or connection strings in source code.
- Keep all environment defaults in `.env.example`.
- Real secrets must stay in `.env`, local shell environment, CI secrets, or external secret managers only.
- Staging infrastructure must be defined as code.
- Infrastructure changes must include validation steps.

## MCP Workflow

- Use MCP for Jira and GitHub automation.
- Use Atlassian MCP for Jira epics, stories, acceptance criteria, issue creation, issue updates, and status checks.
- Use GitHub MCP for repository, issue, pull request, workflow, and release context that is not available from local git.
- Before implementing a Jira story, create or switch to a story branch first using `feature/<jira-key>-<short-slug>`.
- Include the Jira key in branch names and commit messages when the work is tied to a Jira issue.
- Only transition, comment on, or mark Jira subtasks that were explicitly started or directly worked in the current task.
- Do not update sibling subtasks just because their acceptance criteria are related to the same parent story.
- Do not add custom Jira or GitHub REST integration code unless explicitly requested.
- Confirm before creating or bulk-editing Jira issues.
- Confirm before pushing branches, opening pull requests, triggering workflows, or changing issue statuses.
- Never persist Jira, GitHub, or API tokens in repo files.

## Review Guidelines

- Prioritize correctness, regressions, data integrity, authentication, unsafe write paths, and credential exposure.
- Treat accidental credential persistence as a high-priority issue.
- Prefer small, verifiable changes over large rewrites.
