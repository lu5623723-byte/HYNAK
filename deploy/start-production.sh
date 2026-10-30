#!/bin/sh
# Run only after checking the target host, DNS and existing services.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
test -f .env || { echo 'Missing deploy/.env. Run python3 configure.py PUBLIC_HOST_OR_IP first.' >&2; exit 1; }
docker compose version >/dev/null
docker info >/dev/null
# Never print interpolated config: it contains database and setup secrets.
docker compose config --quiet
docker compose up -d --build --wait --wait-timeout 240
docker compose ps
echo 'Containers started. Verify public HTTPS, then initialize the administrator.'
