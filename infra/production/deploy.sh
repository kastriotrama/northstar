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

docker compose --env-file "$environment_file" -f "$compose_file" config --quiet
docker compose --env-file "$environment_file" -f "$compose_file" build api ingestion
docker compose --env-file "$environment_file" -f "$compose_file" up -d --remove-orphans
docker compose --env-file "$environment_file" -f "$compose_file" ps

echo "NorthStar deployment completed"
