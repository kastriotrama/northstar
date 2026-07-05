# Operations

## Local Startup

```sh
cp .env.example .env
docker compose up -d
uvicorn api.main:app --reload
```

## Local Shutdown

```sh
docker compose down
```

Use volume removal only when intentionally resetting local state:

```sh
docker compose down -v
```

## Health Validation

```sh
curl http://localhost:8000/health
```

## Test Validation

```sh
pytest
```

## Logs

Expected log fields:

- Timestamp
- Level
- Service name
- Request ID or job ID when available
- Message
- Error details when applicable

## Secrets

Never commit real secrets.

Allowed:

- `.env.example` with placeholders
- Documentation with placeholder values

Not allowed:

- Real API keys
- Real database passwords
- Real Jira tokens
- Real GitHub tokens
- Production connection strings

## Atlassian MCP API Token Fallback

OAuth is the preferred interactive path for Atlassian Rovo MCP. If Atlassian
support or your organization admin asks you to test API-token authentication,
set `ATLASSIAN_MCP_AUTHORIZATION` locally and enable the
`atlassian_api_token` MCP server in `.codex/config.toml`.

For a personal API token, use:

```sh
export ATLASSIAN_MCP_AUTHORIZATION="Basic $(printf '%s' 'email@example.com:api_token' | base64)"
```

For a service account API key, use:

```sh
export ATLASSIAN_MCP_AUTHORIZATION="Bearer api_key"
```

Do not commit the resulting value.
