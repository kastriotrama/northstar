# Backend Pattern

## Target Structure

```txt
api/
  main.py
  app/
    main.py
    core/
      settings.py
      db.py
      exceptions.py
    features/
      health/
        router.py
        service.py
        schemas.py
      example_feature/
        router.py
        service.py
        repository.py
        schemas.py
    integrations/
      github/
      jira/
      openai/
    shared/
tests/
  unit/
  integration/
```

## Rules

- `api/main.py` is entrypoint-only.
- `api/app/main.py` owns app creation, middleware, and router registration.
- Routers own HTTP behavior.
- Services own business logic.
- Repositories own database access.
- Integrations own external provider details.
- Settings are read only in `api/app/core/settings.py`.
- Tests are mandatory for every backend change.

## Testing Pattern

Every backend feature should include tests.

Use:

- `tests/unit/features/<feature>/`
- `tests/integration/`

Unit tests cover services, utilities, schemas, adapters, and regression behavior.
Integration tests cover FastAPI routing, app startup, database behavior, queues, and provider boundaries.

## Definition Of Done

A backend task is done when:

- Code follows the feature structure.
- Tests are added or updated.
- Relevant tests pass.
- Settings are centralized.
- Secrets are not committed.
- `docs/LAST_CONTEXT.md` is updated.
- Remaining risks are clearly reported.
