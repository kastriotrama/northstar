# NorthStar production deployment

NorthStar uses the same pull/build/restart model as VD-AI without sharing its
containers, database, ports, or working tree. GitHub Actions connects to the
Hetzner host over SSH, fast-forwards `/opt/northstar`, validates Compose, builds
the API and ingestion images, and recreates only the `northstar` Compose project.

## Server layout

- application checkout: `/opt/northstar`;
- public gateway: `http://128.140.71.62:8765`;
- application screen: `/normalization-review`;
- PostgreSQL, Neo4j, Elasticsearch, and Redis: private Compose network only;
- persistent data: named Docker volumes prefixed by the Compose project;
- secrets: `/opt/northstar/.env.production` and
  `/opt/northstar/infra/production/htpasswd`, never Git.

VD-AI continues to own port 80 and the host `vehicle_db`. NorthStar does not
connect to, migrate, or modify that database.

## First deployment

1. Clone the repository into `/opt/northstar`.
2. Copy `infra/production/.env.production.example` to `.env.production` and
   replace every placeholder with a random secret.
3. Create `infra/production/htpasswd` for the web administrator.
4. Run `./infra/production/deploy.sh`.
5. Import a reviewed portable bundle with the tools profile, for example:

   ```sh
   docker compose --env-file .env.production -f docker-compose.production.yml \
     --profile tools run --rm ingestion import-normalization-bundle \
     --file /bundles/019fadda-d238-75d3-8312-142dfdce2612/northstar_ts_normalization_atlas_5000_2026-08-06.xlsx
   ```

6. Verify the authenticated web screen and `/health` response.

## GitHub Actions

The production environment requires these repository secrets:

- `NORTHSTAR_HETZNER_IP`;
- `NORTHSTAR_HETZNER_SSH_KEY`.

Deployment runs after a push to `develop` or by manual workflow dispatch. The
workflow does not create secrets, import datasets, or delete persistent volumes.

## Rollback

Check out the previously verified commit in `/opt/northstar` and run
`./infra/production/deploy.sh`. Persistent volumes are not replaced. Database
restoration is a separate, explicit operation and must use a verified backup.
