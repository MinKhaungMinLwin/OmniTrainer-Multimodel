#!/usr/bin/env bash
set -euo pipefail

backup_dir="${OMNI_BACKUP_DIR:-./data/recovery}"
source_db="${OMNI_RECOVERY_SOURCE_DB:-omni}"
drill_db="${OMNI_RECOVERY_DRILL_DB:-omni_recovery_drill}"

if [[ "$drill_db" != omni_recovery_drill* ]]; then
  echo "OMNI_RECOVERY_DRILL_DB must start with omni_recovery_drill" >&2
  exit 2
fi

mkdir -p "$backup_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$backup_dir/postgres-${stamp}.dump"
manifest="$backup_dir/recovery-${stamp}.sha256"

docker compose exec -T postgres pg_dump -U omni -d "$source_db" --format=custom > "$archive"
shasum -a 256 "$archive" > "$manifest"
shasum -a 256 -c "$manifest"

docker compose exec -T postgres dropdb -U omni --if-exists "$drill_db"
docker compose exec -T postgres createdb -U omni "$drill_db"
docker compose exec -T postgres pg_restore -U omni -d "$drill_db" --exit-on-error < "$archive"
docker compose exec -T postgres psql -U omni -d "$drill_db" -v ON_ERROR_STOP=1 -c \
  "SELECT count(*) AS migration_count FROM alembic_version;"
docker compose exec -T postgres dropdb -U omni "$drill_db"

echo "Recovery drill passed: $manifest"
