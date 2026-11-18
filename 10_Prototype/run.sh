#!/usr/bin/env bash
# Fleet Planning Console — start the prototype (macOS / Linux / Git Bash).
# Opens http://127.0.0.1:8051 in the default browser.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
  echo "Python was not found on PATH. Install Python 3.13 and try again." >&2
  exit 1
fi

PY=$(command -v python3 || command -v python)
export PYTHONIOENCODING=utf-8
exec "$PY" backend/server.py "$@"
