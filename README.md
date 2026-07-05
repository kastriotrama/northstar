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
- Environment variables are read only from `api/app/core/settings.py`.
- No secrets in repo files.

## Health Check

```sh
curl http://localhost:8000/health
```
