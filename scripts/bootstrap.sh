#!/usr/bin/env bash
# 一键装（POSIX）。真正的活儿在 scripts/bootstrap.py —— 这里只找到 Python 并转交。
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${A2N_PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python
exec "$PY" scripts/bootstrap.py "$@"
