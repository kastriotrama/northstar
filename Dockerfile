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

# Angular UI, built here so the server never needs Node installed.
FROM node:24-bookworm-slim AS web-build

ENV NX_DAEMON=false \
    NX_NO_CLOUD=true \
    CI=true

WORKDIR /workspace

COPY package.json package-lock.json nx.json tsconfig.base.json ./
RUN npm ci --no-audit --no-fund

COPY apps/northstar-web ./apps/northstar-web
RUN npx nx build northstar-web --configuration=production

# Gateway: nginx serving the built UI. Its config and password file are mounted
# at runtime (see docker-compose.production.yml), so neither is baked in here.
FROM nginx:1.27-alpine AS web

COPY --from=web-build /workspace/dist/apps/northstar-web/browser /usr/share/nginx/html
