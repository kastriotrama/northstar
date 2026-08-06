# Vehicle Intelligence API

FastAPI backend foundation for vehicle intelligence, ingestion, graph data, search, and automation workflows.

## Local Setup

1. Copy `.env.example` to `.env`.
2. Run `docker compose up -d`.
3. Run `uvicorn api.main:app --reload`.
4. Run `pytest`.

To populate an isolated test database from the portable normalization workbook
and verify the generated results immediately, run:

```bash
northstar-ingest import-normalization-bundle \
  --file outputs/019fadda-d238-75d3-8312-142dfdce2612/northstar_normalization_test_bundle_2026-08-06.xlsx
```

The committed workbook uses synthetic plate/VIN identifiers and is safe for
the public repository; see `docs/normalization-command.md` for the full import
contract, the three additional non-overlapping 1,000-car bundles, and safety
boundaries.

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

The **Rules** tab supports reviewed draft corrections, immutable activation,
and safe re-import with a before/after comparison. See
[`docs/rule-review-workbench.md`](docs/rule-review-workbench.md).
