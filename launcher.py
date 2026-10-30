"""Cross-platform launcher; all runtime data lives outside this program directory."""
import argparse, os, subprocess, sys, threading, time, webbrowser
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def load_env():
    file=ROOT/'.env'
    if file.exists():
        for line in file.read_text(encoding='utf-8').splitlines():
            line=line.strip()
            if line and not line.startswith('#') and '=' in line:
                key,value=line.split('=',1);os.environ.setdefault(key.strip(),value.strip().strip('"').strip("'"))

def main():
    load_env()
    parser=argparse.ArgumentParser();parser.add_argument('--no-browser',action='store_true');args=parser.parse_args()
    if sys.version_info<(3,11):raise SystemExit('Please install Python 3.11 or newer: https://www.python.org/downloads/')
    venv=Path(os.environ.get('HYNAK_VENV_DIR',str(ROOT/'.venv'))).expanduser().resolve();python=venv/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not python.exists():
        import venv as v
        print('Creating local Python environment…');v.create(venv,with_pip=True)
    requirements=(ROOT/'requirements.txt').read_bytes()
    import hashlib
    stamp=venv/'requirements.sha256';checksum=hashlib.sha256(requirements).hexdigest()
    if not stamp.exists() or stamp.read_text()!=checksum:
        print('Installing project dependencies. First run needs internet…')
        subprocess.run([str(python),'-m','pip','install','-r',str(ROOT/'requirements.txt')],check=True)
        stamp.write_text(checksum)
    port=int(os.environ.get('PORT','8765'));base=os.environ.get('PUBLIC_BASE_URL',f'http://127.0.0.1:{port}')
    print(f'HYNAK: {base}\nData: {os.environ.get("HYNAK_DATA_DIR","~/.hynak")}\nKeep this window open. Ctrl+C stops safely.')
    if not args.no_browser:
        def open_when_ready():
            import urllib.request
            for _ in range(60):
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=1) as r:
                        if r.status==200:webbrowser.open(base);return
                except Exception:time.sleep(1)
        threading.Thread(target=open_when_ready,daemon=True).start()
    subprocess.run([str(python),'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port),'--no-access-log'],cwd=ROOT,check=True)
if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:pass
    except Exception as e:
        print(f'Start failed: {e}',file=sys.stderr);input('Press Enter to close…');sys.exit(1)
