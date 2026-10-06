#!/bin/bash
# Compatibility entry for the existing Windows scheduled task.
# Identity and network configuration live in %LOCALAPPDATA%/A2N/node.
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if command -v cygpath >/dev/null 2>&1; then
  ROOT_WIN="$(cygpath -m "$ROOT")"
else
  ROOT_WIN="$ROOT"
fi
PY="${A2N_PY:-$ROOT_WIN/.venv/Scripts/python.exe}"
if [ ! -x "$PY" ]; then
  echo "[desktop-node] Run install.bat first: Python is missing" >&2
  exit 1
fi
cd "$ROOT"
exec "$PY" -u scripts/serve_desktop_node.py --watch-existing
