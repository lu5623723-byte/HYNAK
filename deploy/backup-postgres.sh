#!/bin/sh
# Execute daily on host from deploy/. Stop app briefly for a consistent DB/files pair.
set -eu
umask 077
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
backup_root="${1:?Usage: ./backup-postgres.sh /private/backup/directory}"
case "$backup_root" in /*) ;; *) echo 'Backup path must be absolute.' >&2; exit 1;; esac
mkdir -p "$backup_root"
backup_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="$backup_root/$backup_stamp"
mkdir "$backup_dir"
docker compose stop app
trap 'docker compose start app' EXIT
docker compose exec -T db pg_dump -U hynak -Fc hynak > "$backup_dir/database.dump"
docker compose run --rm --no-deps -T --user 0 --entrypoint tar app czf - -C /data attachments > "$backup_dir/attachments.tar.gz"
echo "Backup created at $backup_dir; copy to encrypted off-host storage."
