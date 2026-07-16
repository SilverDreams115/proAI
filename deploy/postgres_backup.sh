#!/bin/sh
set -eu

backup_dir="${PROAI_BACKUP_DIR:-/backups}"
interval_seconds="${PROAI_BACKUP_INTERVAL_SECONDS:-86400}"
# A failed dump must retry soon, not wait a full interval: sleeping 24h on
# failure means every host restart where postgres is not up yet costs a
# whole day of backups.
retry_seconds="${PROAI_BACKUP_RETRY_SECONDS:-60}"
retention_days="${PROAI_BACKUP_RETENTION_DAYS:-7}"
mkdir -p "$backup_dir"

while true; do
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    backup_file="$backup_dir/proai-${timestamp}.sql.gz"
    tmp_dump="$backup_dir/.proai-${timestamp}.sql.tmp"
    tmp_backup="$backup_dir/.proai-${timestamp}.sql.gz.tmp"
    tmp_success="$backup_dir/.last_success.tmp"
    tmp_success_epoch="$backup_dir/.last_success_epoch.tmp"

    rm -f "$tmp_dump" "$tmp_backup"
    if ! pg_dump --clean --if-exists --no-owner --no-privileges > "$tmp_dump"; then
        rm -f "$tmp_dump" "$tmp_backup"
        sleep "$retry_seconds"
        continue
    fi
    if [ ! -s "$tmp_dump" ]; then
        rm -f "$tmp_dump" "$tmp_backup"
        sleep "$retry_seconds"
        continue
    fi
    gzip -9 < "$tmp_dump" > "$tmp_backup"
    gzip -t "$tmp_backup"
    mv "$tmp_backup" "$backup_file"
    rm -f "$tmp_dump"
    date -u +%Y-%m-%dT%H:%M:%SZ > "$tmp_success"
    date -u +%s > "$tmp_success_epoch"
    mv "$tmp_success" "$backup_dir/.last_success"
    mv "$tmp_success_epoch" "$backup_dir/.last_success_epoch"

    find "$backup_dir" -type f -name 'proai-*.sql.gz' -mtime +"$retention_days" -delete
    sleep "$interval_seconds"
done
