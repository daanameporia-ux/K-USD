#!/bin/sh
# Daily pg_dump into /backups, keeping BACKUP_KEEP_DAYS days. Runs forever inside the `backup` container.
# A dump is kept only if pg_dump succeeded and the archive is valid and complete, so a broken backup can
# never replace (and then rotate away) good ones.
set -eu
set -o pipefail
KEEP="${BACKUP_KEEP_DAYS:-14}"
mkdir -p /backups
trap 'exit 0' TERM INT

while true; do
  stamp=$(date -u +%Y%m%d-%H%M%S)
  file="/backups/kusd-$stamp.sql.gz"
  find /backups -maxdepth 1 -name 'kusd-*.sql.gz.tmp' -delete
  # pg_dump ends a complete plain dump with "-- PostgreSQL database dump complete"
  if pg_dump --no-owner --format=plain | gzip -9 > "$file.tmp" \
     && gzip -t "$file.tmp" \
     && gunzip -c "$file.tmp" | tail -n 5 | grep -q "dump complete"; then
    mv "$file.tmp" "$file"
    touch /backups/.last_ok
    echo "backup ok: $file ($(du -h "$file" | cut -f1))"
    # rotate only after a successful dump: keep KEEP days of good copies
    find /backups -maxdepth 1 -name 'kusd-*.sql.gz' -mtime +"$((KEEP - 1))" -delete
  else
    find /backups -maxdepth 1 -name 'kusd-*.sql.gz.tmp' -delete
    echo "backup FAILED at $stamp" >&2
  fi
  sleep 86400 &
  wait $!
done
