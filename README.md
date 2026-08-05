# Vehicle Intelligence API

FastAPI backend foundation for vehicle intelligence, ingestion, graph data, search, and automation workflows.

## Local Setup

1. Copy `.env.example` to `.env`.
2. Run `docker compose up -d`.
3. Run `uvicorn api.main:app --reload`.
4. Run `pytest`.

For a fresh Python environment:

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Services

- FastAPI API
- Python ingestion CLI
- PostgreSQL
- Neo4j
- Elasticsearch
- Redis

## Development Rules

- New backend features go under `api/app/features/<feature>/`.
- Every feature must include tests.
- Routers own HTTP concerns.
- Services own business logic.
- Repositories own database access.
- Integrations own external APIs.
- API environment variables are read only from `api/app/core/settings.py`.
- Ingestion environment variables are read only from `ingestion/config.py`.
- No secrets in repo files.

## Health Check

```sh
curl http://localhost:8000/health
```

## Ingestion CLI

```sh
northstar-ingest list-commands
northstar-ingest tecdoc
northstar-ingest transportstyrelsen
northstar-ingest normalize --batch-id <source-batch-id>
```

The normalization command is retry-safe, requires an explicit staging batch,
and routes uncertain records to the durable review queue. See
[`docs/normalization-command.md`](docs/normalization-command.md).

## Normalization Review

After normalizing a Transportstyrelsen batch, open
`http://localhost:8000/normalization-review` to search, filter, and inspect up
to 300 sanitized normalization results. See
[`docs/normalization-review-dashboard.md`](docs/normalization-review-dashboard.md).
