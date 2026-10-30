#!/bin/zsh
cd "$(dirname "$0")" || exit 1
export HYNAK_TEST_MODE=1
export HYNAK_DATA_DIR="${HYNAK_DATA_DIR:-$HOME/.hynak-web-test}"
export PORT="${PORT:-8765}"
export PUBLIC_BASE_URL="http://127.0.0.1:$PORT"
exec ./start-mac.command "$@"
