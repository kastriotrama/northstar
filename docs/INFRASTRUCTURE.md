# Infrastructure

## Local Services

The local development stack uses Docker Compose.

Required services:

- PostgreSQL
- Neo4j
- Elasticsearch
- Redis

## Service Purpose

PostgreSQL:

- Relational application data
- Raw staging tables
- Job run tracking
- Review queues

Neo4j:

- Canonical vehicle graph
- Vehicle relationships
- Manufacturer, platform, engine, body, variant, and alias relationships

Elasticsearch:

- Search indexes
- Vehicle and component lookup
- Fast text matching

Redis:

- Cache
- Lightweight queues
- Temporary job state

## Startup Rule

Application services should not process jobs until required datastores are healthy.

## Configuration Rule

Do not hardcode credentials or connection strings in source code.

Use:

- `.env.example` for safe defaults and placeholders
- `.env` for local secrets
- CI secrets for pipeline secrets
- Cloud secret managers for staging and production

## Validation Checklist

- PostgreSQL starts locally.
- Neo4j starts locally.
- Elasticsearch starts locally.
- Redis starts locally.
- Health checks pass.
- App can connect to required datastores.
- `.env.example` includes required settings.
- No real secrets are committed.

## Acceptance Criteria Status

Validated on 2026-07-05:

- Docker Compose starts PostgreSQL, Neo4j, Elasticsearch, and Redis.
- Docker health checks pass for all four datastores.
- Shared local connection strings exist in `.env.example`.
- Staging IaC skeleton covers equivalent PostgreSQL, Neo4j, Elasticsearch, and Redis services through Terraform module boundaries.

## Staging IaC

Staging infrastructure lives in `infra/staging`.

The initial Terraform skeleton defines:

- Region and environment inputs
- Networking inputs
- Sizing inputs for PostgreSQL, Neo4j, Elasticsearch, and Redis
- Secret reference names instead of inline secret values
- Provider-neutral module boundaries for PostgreSQL, Neo4j, Elasticsearch, and Redis

Validate the skeleton with:

```sh
cd infra/staging
terraform fmt -check
terraform init
terraform validate
terraform plan -var-file=terraform.tfvars.example
```
