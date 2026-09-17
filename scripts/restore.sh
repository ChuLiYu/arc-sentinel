#!/usr/bin/env bash
# Restore an Arc Sentinel PostgreSQL backup (SPEC §12).
# Usage:
#   scripts/restore.sh /path/to/arc-sentinel-YYYYMMDD-HHMMSS.sql.gz
#
# Environment (VM .env, see SPEC §17):
#   POSTGRES_USER / POSTGRES_DB
#   POSTGRES_CONTAINER (default "postgres")
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "usage: $0 <backup.sql.gz>" >&2
  exit 1
fi

BACKUP_FILE="$1"
[ -f "$BACKUP_FILE" ] || { echo "ERROR: backup file not found: $BACKUP_FILE" >&2; exit 1; }

CONTAINER="${POSTGRES_CONTAINER:-postgres}"
DB="${POSTGRES_DB:-arc_sentinel}"
PG_USER="${POSTGRES_USER:-arc_sentinel}"

echo "[restore] restoring $BACKUP_FILE into $DB (container $CONTAINER)"
gunzip -c "$BACKUP_FILE" | docker exec -i "$CONTAINER" psql -U "$PG_USER" -d "$DB" --set ON_ERROR_STOP=1
echo "[restore] done"