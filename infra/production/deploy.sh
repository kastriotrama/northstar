#!/bin/sh
set -eu

project_directory=${NORTHSTAR_PROJECT_DIRECTORY:-/opt/northstar}
compose_file=${NORTHSTAR_COMPOSE_FILE:-docker-compose.production.yml}
environment_file=${NORTHSTAR_ENV_FILE:-.env.production}
private_data_directory=${NORTHSTAR_PRIVATE_DATA_DIRECTORY:-/opt/northstar-private}
resolved_showcase_file=$private_data_directory/ts-ktype-resolved-showcase-1000.json

cd "$project_directory"

if [ ! -f "$environment_file" ]; then
    echo "Missing $project_directory/$environment_file" >&2
    exit 1
fi
if [ ! -f infra/production/htpasswd ]; then
    echo "Missing $project_directory/infra/production/htpasswd" >&2
    exit 1
fi
if [ ! -r "$resolved_showcase_file" ]; then
    echo "Missing or unreadable restricted showcase: $resolved_showcase_file" >&2
    exit 1
fi

# The unprivileged nginx workers must be able to read the mounted password-hash
# file. It contains an Apache bcrypt hash, never the clear-text password.
chmod 0644 infra/production/htpasswd

# The code version a person's KType choice records as part of its evidence. It is
# a build argument: the API image carries it, whoever starts the container later.
NORTHSTAR_BUILD_VERSION=${NORTHSTAR_BUILD_VERSION:-$(git rev-parse --short HEAD 2>/dev/null || echo unknown)}
export NORTHSTAR_BUILD_VERSION

docker compose --env-file "$environment_file" -f "$compose_file" config --quiet
docker compose --env-file "$environment_file" -f "$compose_file" build api ingestion gateway
# Schema before code: a new API that reads a column the live table does not have
# yet answers 503 until someone remembers a manual refresh -- which is exactly how
# the first Vehicles release broke. Add-column/create-index only, idempotent and
# fast; the heavy data backfill (refresh-vehicle-facts) stays a deliberate step.
docker compose --env-file "$environment_file" -f "$compose_file" run --rm ingestion migrate-vehicle-facts
# The NorthStar vehicle tables (core.vehicles and friends) the Vehicles tab and the
# TS screen's rule sync read. Schema and constraint check only; filling them is the
# deliberate backfill-vehicle-core / import-ais-vin-export sequence, never a deploy.
# It also creates and verifies core.vehicle_ktype_choices (people's KType choices)
# and core.vehicle_fact_corrections (their corrections of one car's data): a
# missing or disabled constraint or trigger stops the deploy here.
# The same step creates core.vehicle_match_results (the stored outcome of matching
# per car); filling it is the deliberate refresh_vehicle_match_results run.
docker compose --env-file "$environment_file" -f "$compose_file" run --rm ingestion migrate-vehicle-core
docker compose --env-file "$environment_file" -f "$compose_file" up -d --remove-orphans
docker compose --env-file "$environment_file" -f "$compose_file" ps

echo "NorthStar deployment completed"
