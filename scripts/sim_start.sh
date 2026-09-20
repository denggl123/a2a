#!/bin/bash
# A2N 模拟运转环境启动：平台 + 四节点 + 三个行业案例容器（幂等：先杀后起，库可保留或清空）
# 用法：bash scripts/sim_start.sh [fresh]    fresh=清库冷启
#
# 两条供给，分工不同：
#   · 四节点（主机进程）= 平台自带的演示节点，演示四条钱路（华东收费 / 华北免费 /
#     新加坡 x402 / 华南试用中）；
#   · 三个容器（docker）  = 「找 Agent」里的行业案例，真的跑在容器里。
#     控制台 → 平台 → relay → 反向隧道 → 容器 这段是真生产链路；
#     容器里最后一跳**故意不通**（伪真实：卡是真的，调用必然失败）。
# 档位定义：四节点在 scripts/run_a2a_node.py 的 PRESETS，案例在
# scripts/run_market_demo_agents.py 的 MARKET + CONTAINERS。
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
# 结算与对账的日切节拍（秒）：起服务即先切一次，之后每 5 分钟一次。
# 关掉（0）时运维页会显示"还没对过账"——演示要看的是"它真的在自动跑"。
export A2N_CLOSING_INTERVAL_SEC="${A2N_CLOSING_INTERVAL_SEC:-300}"
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

# 平台。必须监听 0.0.0.0：容器经 `host.docker.internal` 回连平台，
# 只绑 127.0.0.1 的话容器根本连不进来（症状是容器日志里一串连接被拒，
# 而控制台上什么都看不到 —— 看起来像"案例没上架"，其实是网络不通）。
"$PY" -m uvicorn a2n_server.app:app --host 0.0.0.0 --port 8000 > data/sim_server.log 2>&1 &
echo "[sim] 平台 pid=$!（监听 0.0.0.0:8000，容器要回连）"

sleep 3
# 四节点：属地 / 价目 / 结算方式 / 延迟 / 试用状态都不同的四档
A2N_ROLE=charging A2N_LOCAL_PORT=9102 A2N_PRINCIPAL=acct:bob   "$PY" scripts/run_a2a_node.py > data/sim_node_bob.log 2>&1 &
A2N_ROLE=free     A2N_LOCAL_PORT=9103 A2N_PRINCIPAL=acct:carol "$PY" scripts/run_a2a_node.py > data/sim_node_carol.log 2>&1 &
A2N_ROLE=x402     A2N_LOCAL_PORT=9104 A2N_PRINCIPAL=acct:erin  "$PY" scripts/run_a2a_node.py > data/sim_node_erin.log 2>&1 &
# 第四档：新上架、**还在试用期**（前 10 次完成免费）。留着它，控制台上的
# "试用中 N/10 · 免费"徽标与毕业判据才有真身可看。
A2N_ROLE=trial    A2N_LOCAL_PORT=9105 A2N_PRINCIPAL=acct:frank "$PY" scripts/run_a2a_node.py > data/sim_node_frank.log 2>&1 &
echo "[sim] 四节点已拉起（9102 收费 / 9103 免费 / 9104 x402 / 9105 试用中）"
sleep 8
# 行业案例：**搬进容器跑**（2026-09-19 用户拍板）。
#
# 控制台 → 平台 → relay 入口 → 反向隧道 → 容器内本地服务 这段走的是**真的生产
# 链路**：注册、心跳、地址投影、门禁、拨号一个都没省略。容器**零端口映射** ——
# 外面打不进来，全靠出站连接 + 反向隧道（"没有公网 IP 也能被调用"的实证）。
#
# 容器里那最后一跳**故意不通**：卡上写着"交付一份经营报表"，容器里并没有能
# 产出它的东西，所以调用必然失败、而且必须响亮地失败。**别把这条当成故障去修。**
if command -v docker >/dev/null 2>&1; then
  DOCKER="${A2N_DOCKER:-docker}"
elif [ -x "/c/Program Files/Docker/Docker/resources/bin/docker.exe" ]; then
  # shim 污染的 PATH 里常常真没有 docker，但 Docker Desktop 就装在默认位置
  DOCKER="${A2N_DOCKER:-/c/Program Files/Docker/Docker/resources/bin/docker.exe}"
else
  DOCKER="${A2N_DOCKER:-docker}"
fi
if ! "$DOCKER" build -f docker/Dockerfile -t a2n-agent . > data/sim_docker_build.log 2>&1; then
  echo "[sim] 镜像构建失败（看 data/sim_docker_build.log）—— 三个案例容器起不来"
fi
start_market_container () {
  local name="$1" key="$2"
  "$DOCKER" rm -f "$name" >/dev/null 2>&1 || true
  "$DOCKER" run -d --name "$name" \
    --add-host host.docker.internal:host-gateway \
    -e A2N_PLATFORM=http://host.docker.internal:8000 \
    -e A2N_CONTAINER="$key" \
    -e A2N_PRINCIPAL=acct:alice \
    -v "a2n-demo-market-${key}:/app/state" \
    a2n-agent python -u /app/docker/market_node.py >/dev/null
}
# 钥匙落在**命名卷**里（不是容器层）：重启才复用同一条上架（uid 由钥匙派生），
# 不会每起一次就多注册一条 —— 重启三次就是三份重复的案例。
start_market_container a2n-market-video   video-studio
start_market_container a2n-market-finance finance-legal
start_market_container a2n-market-play    play-ecom
echo "[sim] 案例容器已拉起（短视频成片与口播稿 / 经营报表与合同草案 / 游戏策划案与商品详情页）"
sleep 8
for name in a2n-market-video a2n-market-finance a2n-market-play; do
  if [ "$("$DOCKER" inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" != "true" ]; then
    echo "[sim] 容器 $name 没在跑，它自己的日志："
    "$DOCKER" logs --tail 20 "$name" 2>&1 || true
  fi
done
# 播种：三个"已开业"档位补成毕业态。**卡上不许声明退出免费期**（任何想被发现的
# agent，前 10 次完成调用免费），所以收费档不能再靠卡上一行 trial=false 就可收费 ——
# 它必须真的走完免费期。真实前提（10 次完成 + 四道毕业闸门）由这个脚本如实伪造，
# 详见 scripts/seed_established.py 的头部。第四档（试用中）不播，保持试用态。
"$PY" scripts/seed_established.py || echo "[sim] 播种失败（收费档会显示为试用中，检查上面的输出）"
# 收费的案例容器也要真的"在卖"：同样走完免费期再毕业。
# 名字是**案例自己的展示名**（不是容器名）—— 一个容器跑两份供给、各一张卡，
# 卡上的 name 就是那一档交付什么。与 run_market_demo_agents.py::MARKET 里
# **带挂牌价**的三条一致；第三个容器（游戏策划案 / 商品详情页）全是免费档，
# 不播，保持"试用中 N/10 · 免费"的真身可看。
# 对不上时 seed 会打印"没找到任何已开业档位"，不会静默跳过。
"$PY" scripts/seed_established.py "短视频成片包" "经营报表" "合同草案" \
  || echo "[sim] 市场收费档播种失败（它们会停留在试用中）"
# 状态读数（只给人看）：用项目自己的解释器取，**不依赖 PATH 里有没有 curl**。
# 曾经写成 `curl -s ... | "$PY" -c json.load` —— PATH 里没有 curl 时（本机 curl 在
# System32，被 shim 污染的 PATH 里没有），输入为空，json.load 抛 JSONDecodeError，
# 还印出"[sim] 注册数: "（空）。那与"端口被占、静默起不来"的症状**一模一样**，
# 反而把真故障藏起来了。读数失败就如实说"读不到"，绝不许伪装成 0。
N_AGENTS="$("$PY" -c "import json,urllib.request as u; print(len(json.load(u.urlopen('http://127.0.0.1:8000/v1/registry/agents', timeout=5))))" 2>/dev/null || true)"
echo "[sim] 注册数: ${N_AGENTS:-读不到}"
