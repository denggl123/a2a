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
NODE_PATH="${NODE_PATH:-C:\\Users\\Administrator\\.workbuddy\\binaries\\node\\workspace\\node_modules}"
export NODE_PATH

export PYTHONPATH="$("$PY" -c "import glob,os;print(';'.join(os.path.abspath(p) for p in sorted(glob.glob('packages/*/src'))))")"

# Every run owns a fresh home and output directory.  A caller-provided OUT is
# a parent directory, never a directory to erase or overwrite.
mkdir -p data
HOME_DIR="$(mktemp -d data/node-console-check-home.XXXXXX)" || exit 1
OUT_ROOT="${OUT:-data/node-console-shots}"
mkdir -p "$OUT_ROOT" || exit 1
RUN_OUT="$(mktemp -d "$OUT_ROOT/run.XXXXXX")" || exit 1

# Prefer an unused ephemeral port.  An explicit port must already be free;
# otherwise the browser test could accidentally mount its demo Agent on a
# person's real node listening there.
NODE_PORT="${NODE_PORT:-$("$PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')}"
if ! "$PY" -c "import socket,sys; s=socket.socket(); r=s.connect_ex(('127.0.0.1', int(sys.argv[1]))); s.close(); sys.exit(0 if r else 1)" "$NODE_PORT"; then
  echo "✗ 端口 $NODE_PORT 已被占用，未运行浏览器测试。"
  exit 1
fi

"$PY" -m a2n_node serve --home "$HOME_DIR" --port "$NODE_PORT" --no-p2p \
  > "$RUN_OUT/daemon.log" 2>&1 &
DAEMON_PID=$!
cleanup() { kill "$DAEMON_PID" 2>/dev/null; wait "$DAEMON_PID" 2>/dev/null; }
trap cleanup EXIT

# Require this very daemon's startup marker and a live process before using
# /health.  A response from an unrelated service is never sufficient.
up=0
for _ in $(seq 1 40); do
  if ! kill -0 "$DAEMON_PID" 2>/dev/null; then break; fi
  if grep -q 'A2N 本机节点已启动' "$RUN_OUT/daemon.log" && \
     "$PY" -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:$NODE_PORT/health', timeout=2).status==200 else 1)" 2>/dev/null; then
    up=1; break
  fi
  sleep 0.5
done
if [ "$up" != 1 ]; then
  echo "✗ 节点没起来，daemon.log 末尾："
  tail -20 "$RUN_OUT/daemon.log"
  exit 1
fi

A2N_NODE_BASE="http://127.0.0.1:$NODE_PORT" OUT="$RUN_OUT" \
  "$NODE_EXE" scripts/node_console_check.js
rc=$?
echo "本次截图与日志：$RUN_OUT"
echo "本次临时节点数据：$HOME_DIR"
exit $rc
