#!/bin/sh
# Source distribution entry for Linux/macOS; bootstrap installs all five packages.
set -eu
task_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'Python 3 is required for the source installer. Use the bundled release when available.' >&2
  exit 1
fi
exec python3 "$task_root/scripts/bootstrap.py" --launch "$@"
