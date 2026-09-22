#!/usr/bin/env bash
set -euo pipefail

env_file="${1:-.env.staging}"

if [[ ! -f "$env_file" ]]; then
  echo "Missing $env_file. Copy deploy/ec2/staging.env.example and fill every value." >&2
  exit 1
fi

chmod 600 "$env_file"

compose=(docker compose --env-file "$env_file" -f compose.staging.yaml)

"${compose[@]}" config --quiet
"${compose[@]}" build migrate seed api worker voice
"${compose[@]}" up -d --wait postgres redis minio
"${compose[@]}" --profile tools run --rm migrate

if grep -Eq '^OMNI_ALLOW_DEV_AUTH=(true|1|yes)$' "$env_file"; then
  "${compose[@]}" --profile tools run --rm seed
fi

"${compose[@]}" up -d --wait api worker voice caddy
"${compose[@]}" ps
