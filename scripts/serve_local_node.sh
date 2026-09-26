#!/bin/bash
# 本机节点（宿主机上的那"1 本机"）的生产形态启动器。
#
# 与公网机上那个 `/opt/a2n/start_node.sh` **同一个形状**：加载配置 → 起
# `scripts/serve_public_node.py`（真 a2n-node：控制台 + P2P + 公益开关）。
# 差别只有两处：本机用 `.venv` 里的解释器；本机**不注入 A2N_STORAGE_KEY**
# （Windows 上凭据走 DPAPI，见 scripts/local_node.env 顶部说明）。
#
# 为什么要一个启动器而不是直接敲命令：Windows 没有 systemd，监管者只能搭在
# **计划任务 / 服务**上，而那些东西需要一个"可被反复调用的、幂等的入口"——
# 就是这个脚本。手动跑、计划任务跑、将来换任何监管者跑，走的都是同一条路径。
#
# 用法：
#   bash scripts/serve_local_node.sh              # 前台常驻
#   bash scripts/serve_local_node.sh &            # 手动后台（**临时**，会话没了就没了）
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if command -v cygpath >/dev/null 2>&1; then
  ROOT_WIN="$(cygpath -m "$ROOT")"
else
  ROOT_WIN="$ROOT"
fi

# 与跑测试、跑其它脚本用**同一个**解释器：项目 .venv
PY="${A2N_PY:-$ROOT_WIN/.venv/Scripts/python.exe}"
if [ ! -x "$PY" ]; then
  echo "[local-node] 找不到解释器 $PY —— 先 bash scripts/install_all.sh 建 .venv" >&2
  exit 1
fi

# 配置（端口/档位，无密钥）。`set -a` 让文件里的赋值直接成为环境变量。
if [ -f "$ROOT/scripts/local_node.env" ]; then
  set -a
  . "$ROOT/scripts/local_node.env"
  set +a
fi

# 兜底默认值：配置缺项也能起来，且与 local_node.env 写的一致
export A2N_HOME="${A2N_HOME:-$ROOT_WIN/data/node-local}"
export A2N_PUBLIC_BASE="${A2N_PUBLIC_BASE:-http://127.0.0.1:8891}"
export A2N_PUBLIC_PORT="${A2N_PUBLIC_PORT:-8891}"
export A2N_PORT="${A2N_PORT:-8890}"
export A2N_P2P_PORT="${A2N_P2P_PORT:-9788}"
export A2N_CONTAINER="${A2N_CONTAINER-}"
export A2N_PUBLIC_SERVICE="${A2N_PUBLIC_SERVICE:-1}"

# 本机常驻 HTTP 代理会拦 127.0.0.1（症状是 502 upstream connect failed）——
# 本机节点全程回环，明确绕过代理。
export NO_PROXY="127.0.0.1,localhost"
export no_proxy="$NO_PROXY"

mkdir -p "$A2N_HOME"
cd "$ROOT"
# ⚠ 控制台要用**节点自己的回环端口**（A2N_PORT），不是 A2N_PUBLIC_BASE：
#   后者指向同机反代，而反代**故意只放行 /a2a/* 与 /public/*** ——
#   拿反代地址去开 /console 只会得到 404（管理面不外放，这是设计）。
echo "[local-node] 起节点：HOME=$A2N_HOME" >&2
echo "[local-node] 控制台（本机打开）：http://127.0.0.1:$A2N_PORT/console" >&2
echo "[local-node] 公开目录（别的节点填这个）：$A2N_PUBLIC_BASE" >&2
exec "$PY" -u scripts/serve_public_node.py
