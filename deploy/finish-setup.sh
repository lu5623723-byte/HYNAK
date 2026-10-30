#!/bin/sh
# Remove the bootstrap token only after an active administrator exists.
set -eu
umask 077
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
docker compose exec -T app python - <<'PY'
from sqlalchemy import select, func
from app.db import Session
from app.models import User
with Session() as db:
    assert db.scalar(select(func.count(User.id)).where(User.role=='admin',User.active==True)), 'Create an active administrator first'
print('Administrator exists; retiring the bootstrap token.')
PY
python3 - <<'PY'
import os, tempfile
from pathlib import Path
p=Path('.env'); old=p.read_text()
new=''.join(line for line in old.splitlines(True) if not line.startswith('SETUP_TOKEN='))
fd,name=tempfile.mkstemp(dir='.')
with os.fdopen(fd,'w') as stream: stream.write(new)
os.chmod(name,0o600)
os.replace(name,p)
PY
docker compose up -d --no-deps --force-recreate --wait --wait-timeout 120 app
echo 'Bootstrap token removed. Log in using the administrator you created.'
