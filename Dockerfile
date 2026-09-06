# Shared base: install dependencies once, reused by both service images.
FROM python:3.11-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/apps/backend

WORKDIR /app

# Dependencies in their own layer: code changes must not re-download packages.
COPY apps/backend/pyproject.toml ./apps/backend/
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('apps/backend/pyproject.toml','rb'))['project']['dependencies']))" \
    > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt

COPY apps/backend/api ./apps/backend/api
COPY apps/backend/ingestion ./apps/backend/ingestion
COPY apps/backend/northstar ./apps/backend/northstar
COPY apps/backend/scripts ./apps/backend/scripts

RUN pip install --no-deps ./apps/backend \
    && useradd --create-home appuser

USER appuser

# Request-serving FastAPI image.
# EXPOSE is documentation only; remap the host port at runtime (-p host:8000).
FROM base AS api

EXPOSE 8000
CMD ["/bin/sh", "-c", "exec uvicorn api.main:app --host ${API_HOST:-0.0.0.0} --port ${API_PORT:-8000}"]

# Batch ingestion CLI image; pass a job name as the command.
FROM base AS ingestion

ENTRYPOINT ["python", "-m", "ingestion.cli"]
CMD ["list-commands"]
