#!/usr/bin/env bash
set -euo pipefail

env_file="${1:-.env.staging}"

if ! docker buildx version >/dev/null 2>&1; then
  echo "Docker Buildx 0.17.0 or later is required by Docker Compose." >&2
  exit 1
fi

if [[ ! -f "$env_file" ]]; then
  echo "Missing $env_file. Copy deploy/ec2/staging.env.example and fill every value." >&2
  exit 1
fi

chmod 600 "$env_file"

# The staging host is intentionally small. Build sequentially to avoid exhausting
# memory while Compose prepares the API, worker, and voice images.
export COMPOSE_PARALLEL_LIMIT="${COMPOSE_PARALLEL_LIMIT:-1}"

compose=(docker compose --env-file "$env_file" -f compose.staging.yaml)

"${compose[@]}" config --quiet
"${compose[@]}" build migrate seed api worker voice
"${compose[@]}" up -d --wait postgres redis minio phoenix
"${compose[@]}" --profile tools run --rm migrate

if grep -Eq '^OMNI_ALLOW_DEV_AUTH=(true|1|yes)$' "$env_file"; then
  "${compose[@]}" --profile tools run --rm seed
fi

"${compose[@]}" up -d --wait phoenix api worker voice caddy
"${compose[@]}" ps
