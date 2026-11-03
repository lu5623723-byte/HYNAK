"""One content version for the shared UI used by every account role."""
import hashlib
from functools import lru_cache
from pathlib import Path

ROOT=Path(__file__).parent
ASSETS=('static/app.css','static/ranking-charts.js','static/inventory-scope.js',
        'static/session-sync.js','static/policy-assistant.js','static/smart-workbench.js','static/app.js','templates/index.html')


@lru_cache(maxsize=16)
def _version(files):
    digest=hashlib.sha256()
    for name,modified,size in files:
        digest.update(name.encode());digest.update(Path(name).read_bytes())
    return digest.hexdigest()[:16]


def frontend_version():
    return _version(tuple((str(ROOT/name),(ROOT/name).stat().st_mtime_ns,
                           (ROOT/name).stat().st_size) for name in ASSETS))
