#!/usr/bin/env bash
# 本机节点控制台（runtime.html）浏览器级核验的编排器：
#   临时 home 起一个**真** a2n-node（--no-p2p、不连平台 = 个人电脑最小形态）
#   → 跑 scripts/node_console_check.js（渲染/交互/截图/响应式）
#   → 无论成败都停掉 daemon，退出码取 JS 的。
# 平台控制台的五层（console_* / ui_check）不覆盖这个页面，这里是它的等价物。
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="/usr/bin:/bin:/c/Windows/System32:$PATH"
export NO_PROXY=127.0.0.1,localhost

PY="${PYTHON:-.venv/Scripts/python.exe}"
NODE_EXE="${NODE_EXE:-C:/Users/Administrator/.workbuddy/binaries/node/versions/22.22.2-3/node.exe}"
NODE_PORT="${NODE_PORT:-8771}"
NODE_PATH="${NODE_PATH:-C:\\Users\\Administrator\\.workbuddy\\binaries\\node\\workspace\\node_modules}"
export NODE_PATH

export PYTHONPATH="$("$PY" -c "import glob,os;print(';'.join(os.path.abspath(p) for p in sorted(glob.glob('packages/*/src'))))")"

HOME_DIR="data/node-console-check-home"
OUT="${OUT:-data/node-console-shots}"
rm -rf "$HOME_DIR" "$OUT" 2>/dev/null
mkdir -p "$OUT"

"$PY" -m a2n_node serve --home "$HOME_DIR" --port "$NODE_PORT" --no-p2p \
  > "$OUT/daemon.log" 2>&1 &
DAEMON_PID=$!
cleanup() { kill "$DAEMON_PID" 2>/dev/null; wait "$DAEMON_PID" 2>/dev/null; }
trap cleanup EXIT

# 等网关起来（/health 连续可访问；最多 ~20s）。用 python 而不是 curl：不依赖外部工具。
up=0
for _ in $(seq 1 40); do
  if "$PY" -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:$NODE_PORT/health', timeout=2).status==200 else 1)" 2>/dev/null; then
    up=1; break
  fi
  sleep 0.5
done
if [ "$up" != 1 ]; then
  echo "✗ 节点没起来，daemon.log 末尾："
  tail -20 "$OUT/daemon.log"
  exit 1
fi

A2N_NODE_BASE="http://127.0.0.1:$NODE_PORT" OUT="$OUT" \
  "$NODE_EXE" scripts/node_console_check.js
rc=$?
exit $rc
