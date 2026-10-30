#!/bin/zsh
cd "$(dirname "$0")" || exit 1
for task_python in /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"; do
  if [[ -x "$task_python" ]] && "$task_python" -c 'import sys; assert sys.version_info >= (3,11)' 2>/dev/null; then
    exec "$task_python" launcher.py "$@"
  fi
done
if command -v python3 >/dev/null && python3 -c 'import sys; assert sys.version_info >= (3,11)' 2>/dev/null; then
  exec python3 launcher.py "$@"
fi
print 'Please install Python 3.11+ from https://www.python.org/downloads/macos/ then double-click again.'
read 'task_answer?Press Enter to close…'
