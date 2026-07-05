# CI Requirements

## Required Checks

CI should run:

- Lint
- Type checks
- Unit tests
- Integration tests
- App import/startup validation where practical
- Docker image build where applicable

## Mandatory Test Policy

CI must run tests on every pull request.

```sh
pytest
```

A pull request should not be merged when:

- Unit tests fail.
- Integration tests fail.
- New backend behavior has no test coverage.
- A bug fix has no regression test and no explanation.

## Secrets

CI must not require production secrets.

Allowed:

- Test credentials
- Ephemeral service credentials
- CI-managed secrets for external integrations when needed

Not allowed:

- Hardcoded real tokens
- Production database credentials
- Production API keys in repo files
