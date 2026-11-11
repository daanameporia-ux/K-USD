#!/bin/sh
# Daily pg_dump into /backups, keeping BACKUP_KEEP_DAYS days. Runs forever inside the `backup` container.
set -eu
KEEP="${BACKUP_KEEP_DAYS:-14}"
mkdir -p /backups
while true; do
  stamp=$(date -u +%Y%m%d-%H%M%S)
  file="/backups/kusd-$stamp.sql.gz"
  if pg_dump --no-owner --format=plain | gzip -9 > "$file.tmp"; then
    mv "$file.tmp" "$file"
    echo "backup ok: $file ($(du -h "$file" | cut -f1))"
  else
    rm -f "$file.tmp"
    echo "backup FAILED at $stamp" >&2
  fi
  find /backups -name 'kusd-*.sql.gz' -mtime +"$KEEP" -delete
  sleep 86400
done
