#!/bin/sh
set -eu

backup_dir="${PROAI_BACKUP_DIR:-/backups}"
interval_seconds="${PROAI_BACKUP_INTERVAL_SECONDS:-86400}"
retention_days="${PROAI_BACKUP_RETENTION_DAYS:-7}"
mkdir -p "$backup_dir"

while true; do
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    backup_file="$backup_dir/proai-${timestamp}.sql.gz"
    tmp_success="$backup_dir/.last_success.tmp"

    pg_dump --clean --if-exists --no-owner --no-privileges | gzip -9 > "$backup_file"
    date -u +%Y-%m-%dT%H:%M:%SZ > "$tmp_success"
    mv "$tmp_success" "$backup_dir/.last_success"

    find "$backup_dir" -type f -name 'proai-*.sql.gz' -mtime +"$retention_days" -delete
    sleep "$interval_seconds"
done
