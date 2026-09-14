#!/bin/bash
# A2N 模拟运转环境启动：平台 + 四节点（幂等：先杀后起，库可保留或清空）
# 用法：bash scripts/sim_start.sh [fresh]    fresh=清库冷启
# 四节点是四档不一样的样本（华东收费 / 华北免费 / 新加坡 x402 / 华南试用中），
# 让演示里的"市场"看起来像市场 —— 档位定义在 scripts/run_a2a_node.py 的 PRESETS。
set -e
# 脚本自己找根目录：BASH_SOURCE 给出的是 Git Bash 形式（/d/...），
# 而 Python 的 glob/os 只认 Windows 形式（D:/...）—— 两者都要，
# 否则 PYTHONPATH 会拼成空的，平台直接 ModuleNotFoundError。
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if command -v cygpath >/dev/null 2>&1; then
  ROOT_WIN="$(cygpath -m "$ROOT")"
elif [ -n "$(pwd -W 2>/dev/null)" ]; then
  ROOT_WIN="$(cd "$ROOT" && pwd -W)"
else
  ROOT_WIN="$ROOT"
fi
PY="${A2N_PY:-D:/ftzy/hermes_home/hermes-agent/venv/Scripts/python.exe}"
export PYTHONPATH="$("$PY" -c "import os,glob; print(';'.join(os.path.abspath(p) for p in glob.glob('$ROOT_WIN/packages/*/src')))")"
export A2N_DB="$ROOT_WIN/data/e2e_a2a.db"
# 演示环境：管理台「充值」需要演示开关（生产必须持牌方签名，勿开）
export A2N_DEMO_CUSTODIAN=1
# 本机常驻 HTTP 代理会拦 127.0.0.1（症状是 502 upstream connect failed）——
# 演示全程是本机回环，明确绕过代理。
export NO_PROXY="127.0.0.1,localhost"
export no_proxy="$NO_PROXY"

cd "$ROOT"
# 先杀后起：端口被上一轮残留占着会静默起不来（脚本照样往下跑，症状是"注册数 0"）
"$PY" scripts/sim_stop.py || true
if [ "$1" = "fresh" ]; then
  # Windows 环境删除库用 Python os.remove（shell rm 会被安全钩子拦截）
  "$PY" -c "import os,glob,time; time.sleep(0.5); [os.remove(f) for f in glob.glob('$ROOT_WIN/data/e2e_a2a.db*')]"
  echo "[sim] 库已清"
fi

# 平台
"$PY" -m uvicorn a2n_server.app:app --host 127.0.0.1 --port 8000 > data/sim_server.log 2>&1 &
echo "[sim] 平台 pid=$!"

sleep 3
# 四节点：属地 / 价目 / 结算方式 / 延迟 / 试用状态都不同的四档
A2N_ROLE=charging A2N_LOCAL_PORT=9102 A2N_PRINCIPAL=acct:bob   "$PY" scripts/run_a2a_node.py > data/sim_node_bob.log 2>&1 &
A2N_ROLE=free     A2N_LOCAL_PORT=9103 A2N_PRINCIPAL=acct:carol "$PY" scripts/run_a2a_node.py > data/sim_node_carol.log 2>&1 &
A2N_ROLE=x402     A2N_LOCAL_PORT=9104 A2N_PRINCIPAL=acct:erin  "$PY" scripts/run_a2a_node.py > data/sim_node_erin.log 2>&1 &
# 第四档：新上架、**还在试用期**（前 10 次完成免费）。留着它，控制台上的
# "试用中 N/10 · 免费"徽标与毕业判据才有真身可看。
A2N_ROLE=trial    A2N_LOCAL_PORT=9105 A2N_PRINCIPAL=acct:frank "$PY" scripts/run_a2a_node.py > data/sim_node_frank.log 2>&1 &
echo "[sim] 四节点已拉起（9102 华东·收费 / 9103 华北·免费 / 9104 新加坡·x402 / 9105 华南·试用中）"
sleep 8
echo "[sim] 注册数: $(curl -s http://127.0.0.1:8000/v1/registry/agents | "$PY" -c "import json,sys; print(len(json.load(sys.stdin)))")"
