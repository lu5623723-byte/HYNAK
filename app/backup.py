import hashlib, json, os, sqlite3, tempfile, zipfile
from datetime import datetime, timezone
from pathlib import Path
from .db import DATA_DIR, engine

def backup(force=False):
    if engine.dialect.name!='sqlite':return {'status':'external_postgresql_backup_required'}
    dest=DATA_DIR/'backups';dest.mkdir(exist_ok=True)
    day=datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if not force and list(dest.glob(day+'-*.zip')):return {'status':'already_backed_up'}
    with tempfile.TemporaryDirectory() as tmp:
        snap=Path(tmp)/'hynak.sqlite3'
        with sqlite3.connect(engine.url.database) as source, sqlite3.connect(snap) as target:source.backup(target)
        archive=dest/(day+'-'+datetime.now(timezone.utc).strftime('%H%M%S%f')+'.zip')
        temporary=archive.with_suffix('.tmp');manifest={}
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as z:
            files=[('hynak.sqlite3',snap)]+[('attachments/'+f.name,f) for f in (DATA_DIR/'attachments').iterdir() if f.is_file()]
            for name,path in files:
                raw=path.read_bytes();manifest[name]=hashlib.sha256(raw).hexdigest();z.writestr(name,raw)
            z.writestr('manifest.json',json.dumps(manifest))
        os.replace(temporary,archive)
    return {'status':'ok','file':archive.name}
def restore(archive,destination):
    dest=Path(destination).expanduser().resolve()
    if dest.exists() and any(dest.iterdir()):raise ValueError('Restore only to an empty isolated directory')
    dest.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        manifest=json.loads(z.read('manifest.json'))
        for name,checksum in manifest.items():
            path=(dest/name).resolve()
            if not path.is_relative_to(dest):raise ValueError('Invalid archive path')
            raw=z.read(name)
            if hashlib.sha256(raw).hexdigest()!=checksum:raise ValueError('Backup checksum mismatch')
        for name in manifest:
            path=dest/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(z.read(name))
    with sqlite3.connect(dest/'hynak.sqlite3') as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Database integrity check failed')
    return dest
