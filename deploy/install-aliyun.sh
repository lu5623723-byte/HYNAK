#!/bin/bash
# Existing Alibaba Cloud Linux 3 host; run as root from Workbench.
# No purchased resources, firewall resets, Docker-data deletion or sample users.
set -Eeuo pipefail
umask 077
[[ $EUID -eq 0 ]] || { echo 'Run this script in the root Workbench terminal.' >&2; exit 1; }
source_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
install_root=/opt/hynak
public_host="${1:-47.106.123.56}"
[[ "$public_host" == 47.106.123.56 ]] || { echo 'This deployment bundle targets 47.106.123.56.' >&2; exit 1; }
. /etc/os-release
[[ "$ID" == alinux && "${VERSION_ID%%.*}" == 3 && "$(uname -m)" == x86_64 ]] || {
    echo 'Expected Alibaba Cloud Linux 3 x86_64. No changes made.' >&2; exit 1;
}
mkdir -p /var/log/hynak
chmod 700 /var/log/hynak
run_log="/var/log/hynak/install-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$run_log") 2>&1
trap 'echo "Installation stopped at line $LINENO. Review $run_log. Existing data volumes are retained." >&2' ERR
echo '[1/7] Checking the installation directory and web ports'
if [[ -e "$install_root" ]]; then
    [[ -f "$install_root/deploy/.aliyun-workbench-install-v1" ]] || {
        echo '/opt/hynak already exists and was not created by this installer. Stopping.' >&2; exit 1;
    }
    cmp -s "$source_root/app/main.py" "$install_root/app/main.py" || {
        echo 'Installed application differs from this bundle. An upgrade needs a backup and review.' >&2; exit 1;
    }
else
    if [[ -n "$(ss -H -ltn '( sport = :80 or sport = :443 )')" ]]; then
        echo 'Port 80 or 443 is already in use. Existing services have been preserved.' >&2
        ss -ltnp '( sport = :80 or sport = :443 )'
        exit 1
    fi
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        [[ -z "$(docker ps -aq --filter label=com.docker.compose.project=hynak)" ]] || {
            echo 'An existing hynak Compose project was found. Stopping.' >&2; exit 1;
        }
    fi
    cp -a "$source_root" "$install_root"
    touch "$install_root/deploy/.aliyun-workbench-install-v1"
fi
cd "$install_root/deploy"
echo '[2/7] Installing Docker and the Compose plugin if required'
command -v curl >/dev/null 2>&1 || dnf -y install curl ca-certificates
if ! command -v docker >/dev/null 2>&1; then
    dnf -y install dnf-plugins-core
    dnf -y install dnf-plugin-releasever-adapter --repo alinux3-plus
    if [[ ! -f /etc/yum.repos.d/docker-ce.repo ]]; then
        dnf config-manager --add-repo=https://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo
    fi
    dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
if ! docker compose version >/dev/null 2>&1; then
    dnf -y install docker-compose-plugin
fi
systemctl enable --now docker
docker version --format 'Docker server: {{.Server.Version}}'
docker compose version
echo '[3/7] Generating private production settings'
command -v python3 >/dev/null 2>&1 || dnf -y install python3
if [[ ! -f .env ]]; then
    python3 configure.py "$public_host"
fi
python3 - "$public_host" <<'PY'
import os, re, sys
from pathlib import Path
p=Path('.env')
values=dict(line.split('=',1) for line in p.read_text().splitlines() if line and not line.startswith('#'))
assert values.get('DOMAIN')==sys.argv[1], 'Existing configuration points to a different host'
assert values.get('CADDY_CONFIG')=='./Caddyfile.ip', 'IP TLS configuration required'
assert re.fullmatch(r'[A-Za-z0-9_-]{40,}', values.get('DB_PASSWORD','')), 'Invalid database credential format'
assert values.get('HYNAK_TEST_MODE','0')!='1', 'Demo mode must stay disabled'
os.chmod(p,0o600)
print('Production configuration checked; secrets were not printed.')
PY
docker compose config --quiet
echo '[4/7] Pulling images and building the application (may take several minutes)'
if [[ "${HYNAK_OFFLINE_IMAGES:-0}" == 1 ]]; then
    offline_refs="$(docker compose config --images)"
    while IFS= read -r image_ref; do
        docker image inspect "$image_ref" >/dev/null
    done <<< "$offline_refs"
    echo 'Offline images already loaded and application already built; registry download skipped.'
else
    docker compose pull db proxy
    docker compose build --pull app
fi
docker compose run --rm --no-deps -T proxy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
echo '[5/7] Starting PostgreSQL, application and HTTPS proxy'
if [[ "${HYNAK_OFFLINE_IMAGES:-0}" == 1 ]]; then
    docker compose up -d --no-build --pull never --wait --wait-timeout 240
else
    docker compose up -d --wait --wait-timeout 240
fi
docker compose exec -T app python - <<'PY'
import os
from sqlalchemy import text
from app.db import engine
assert os.environ.get('HYNAK_TEST_MODE','0')!='1'
assert engine.dialect.name=='postgresql'
with engine.connect() as db:
    assert db.scalar(text('SELECT max(version) FROM schema_versions'))==10
print('PostgreSQL connection and schema version 10 verified.')
PY
echo '[6/7] Creating and checking a consistent database/attachment backup'
mkdir -p /var/backups/hynak
chmod 700 /var/backups/hynak
sh backup-postgres.sh /var/backups/hynak
latest_backup="$(find /var/backups/hynak -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1)"
test -s "$latest_backup/database.dump"
gzip -t "$latest_backup/attachments.tar.gz"
(
    restore_db="hynak_verify_$(date -u +%Y%m%d%H%M%S)"
    docker compose exec -T db createdb -U hynak "$restore_db"
    trap 'docker compose exec -T db dropdb -U hynak "$restore_db"' EXIT
    docker compose exec -T db pg_restore -U hynak --exit-on-error --dbname="$restore_db" < "$latest_backup/database.dump"
    docker compose exec -T db psql -U hynak -d "$restore_db" -Atc 'SELECT max(version) FROM schema_versions' | grep -qx 10
)
echo 'Database restored successfully into a separate temporary verification database.'
if [[ ! -e /etc/systemd/system/hynak-backup.service && ! -e /etc/systemd/system/hynak-backup.timer ]]; then
    cat > /etc/systemd/system/hynak-backup.service <<'UNIT'
[Unit]
Description=HYNAK consistent PostgreSQL and attachment backup
Requires=docker.service
After=docker.service
[Service]
Type=oneshot
UMask=0077
WorkingDirectory=/opt/hynak/deploy
ExecStart=/bin/sh /opt/hynak/deploy/backup-postgres.sh /var/backups/hynak
UNIT
    cat > /etc/systemd/system/hynak-backup.timer <<'UNIT'
[Unit]
Description=Daily HYNAK backup at 03:30 Sao Paulo time
[Timer]
OnCalendar=*-*-* 03:30:00 America/Sao_Paulo
RandomizedDelaySec=15m
Persistent=true
[Install]
WantedBy=timers.target
UNIT
    systemctl daemon-reload
    systemctl enable --now hynak-backup.timer
fi
echo '[7/7] Waiting for an IP certificate and validating HTTPS locally'
tls_ready=0
for attempt in {1..24}; do
    if curl --noproxy '*' --resolve "$public_host:443:127.0.0.1" --fail --silent --show-error --max-time 5 "https://$public_host/health" > /dev/null 2>&1; then
        tls_ready=1
        break
    fi
    sleep 5
done
if [[ "$tls_ready" != 1 ]]; then
    echo 'Containers started, but a trusted IP HTTPS connection is not ready.' >&2
    echo 'Run: cd /opt/hynak/deploy && docker compose logs --tail=60 proxy' >&2
    echo 'Do not send deploy/.env or initialization tokens in screenshots.' >&2
    exit 1
fi
python3 - <<'PY'
from pathlib import Path
import os, tempfile
p=Path('/root/.ssh/authorized_keys')
if p.exists():
    old=p.read_text()
    key='AAAAC3NzaC1lZDI1NTE5AAAAIFs/IdNYwzyRYwtVfHMpRs8RJtnGIzUmR9GCYoVXkFuV'
    new=''.join(line for line in old.splitlines(True) if key not in line)
    if old!=new:
        fd,name=tempfile.mkstemp(dir=str(p.parent))
        with os.fdopen(fd,'w') as stream: stream.write(new)
        os.chmod(name,0o600)
        os.replace(name,p)
        print('Removed the temporary HYNAK deployment public key.')
PY
docker compose ps
echo "HYNAK server installation complete: https://$public_host/"
echo 'Local trusted HTTPS verified. Public access and business acceptance still need verification.'
echo 'For first administrator setup, read SETUP_TOKEN privately from /opt/hynak/deploy/.env.'
echo 'Do not share the token, password or .env. After setup run finish-setup.sh.'
echo 'Daily backups are on this server; protected off-host copies and alerting are still required.'
echo "Private installation log: $run_log"
