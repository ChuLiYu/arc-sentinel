#!/usr/bin/env bash
# Daily PostgreSQL backup for Arc Sentinel (SPEC §12).
# Runs `pg_dump | gzip` inside the postgres container and uploads to OCI
# Object Storage via rclone when configured. Retention: 7 daily + 4 weekly.
#
# Usage:
#   scripts/backup.sh [--dir DIR]     # local-only dump (no upload)
#   scripts/backup.sh                 # local dump + rclone upload when present
# Environment (VM .env, see SPEC §17):
#   POSTGRES_USER / POSTGRES_DB
#   RCLONE_REMOTE  (optional, e.g. "oci-backup:arc-sentinel") — skip upload if empty
set -euo pipefail

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/arc-sentinel}"
mkdir -p "$BACKUP_DIR"

CONTAINER="${POSTGRES_CONTAINER:-postgres}"
DB="${POSTGRES_DB:-arc_sentinel}"
PG_USER="${POSTGRES_USER:-arc_sentinel}"

DAILY_FILE="$BACKUP_DIR/arc-sentinel-$STAMP.sql.gz"
WEEKLY_FILE="$BACKUP_DIR/arc-sentinel-weekly-$STAMP.sql.gz"

echo "[backup] dumping $DB from container $CONTAINER → $DAILY_FILE"
docker exec "$CONTAINER" pg_dump -U "$PG_USER" -d "$DB" --no-owner | gzip > "$DAILY_FILE"

# Weekly copy on Sundays (keep one per week, 4 total)
if [ "$(date +%u)" = "7" ]; then
  echo "[backup] weekly snapshot → $WEEKLY_FILE"
  cp "$DAILY_FILE" "$WEEKLY_FILE"
fi

# Prune: keep 7 daily, 4 weekly
find "$BACKUP_DIR" -maxdepth 1 -name 'arc-sentinel-*.sql.gz' ! -name 'arc-sentinel-weekly-*' | sort | head -n -7 | xargs -r rm -f
find "$BACKUP_DIR" -maxdepth 1 -name 'arc-sentinel-weekly-*.sql.gz' | sort | head -n -4 | xargs -r rm -f

# Optional upload to OCI Object Storage (rclone, VM-only credentials)
if [ -n "${RCLONE_REMOTE:-}" ] && command -v rclone >/dev/null 2>&1; then
  echo "[backup] uploading to $RCLONE_REMOTE"
  rclone copy "$DAILY_FILE" "$RCLONE_REMOTE" --include '*.sql.gz'
fi

echo "[backup] done: $(ls -lh "$DAILY_FILE" | awk '{print $5}')"