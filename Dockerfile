# Shared base: install dependencies once, reused by both service images.
FROM python:3.11-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies in their own layer: code changes must not re-download packages.
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" \
    > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt

COPY api ./api
COPY ingestion ./ingestion
COPY northstar ./northstar

RUN pip install --no-deps . \
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
