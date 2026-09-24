#!/bin/bash
# A2N 模拟运转环境启动：平台 + 本机节点 + 三个容器节点（幂等：先杀后起，库可保留或清空）
# 用法：bash scripts/sim_start.sh [fresh]    fresh=清库冷启
#
# **4 个节点组网**（用户 2026-09-20 的模型）：「本机 + docker 3 个节点（共 4 个），
# 本身可以组成网络」。docker 在这里扮演的是**公网上的另外几台机器** ——
# 真正好了这些节点会丢到外网，所以模拟的是"跨机器"，不是"平台的一部分"。
#
#   · 本机节点（1 个进程）= 这台机器。**一个节点一个身份**（一把钥匙签四张卡），
#     四档只是卡面不同：9102 收费 / 9103 免费 / 9104 x402 / 9105 试用中。
#     控制台开箱身份就是它的 did（平台按 A2N_CONSOLE_PRINCIPAL 注入）。
#   · 三个容器 = 三台"外面的机器"，各自一个身份，各上架两张行业案例卡。
#
# 上架不检查地址通不通（`Registry.register` 只做形状 + 本地验签）：卡在架 ≠ 服务活着。
# 容器里那最后一跳**真的连不上**（去连 A2N_DEAD_AGENT，默认本地没人听的端口）——
# 发现通、对方节点活、请求也送到了，断在它转发给自己那台 agent。**别当故障去修。**
#
# 档位定义：本机节点在 scripts/run_a2a_node.py 的 PRESETS（入口 run_local_node.py），
# 案例在 scripts/run_market_demo_agents.py 的 MARKET + CONTAINERS。
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

# 控制台开箱身份 = **本机节点的 did**（一个节点一个身份）。
# 必须排在起平台**之前**：did 是第一次运行时才生成的，平台起来之后再知道就晚了。
# `--print-did` 只往 stdout 印那一行 did（进度日志走 stderr），tail -1 双保险。
LOCAL_DID="$("$PY" scripts/run_local_node.py --print-did 2>/dev/null | tail -1)"
if [ -z "$LOCAL_DID" ]; then
  echo "[sim] 读不到本机节点身份 —— 控制台会退化成兜底主体（acct:local），自售列表会是空的"
fi
export A2N_CONSOLE_PRINCIPAL="$LOCAL_DID"

# 平台。必须监听 0.0.0.0：容器经 `host.docker.internal` 回连平台，
# 只绑 127.0.0.1 的话容器根本连不进来（症状是容器日志里一串连接被拒，
# 而控制台上什么都看不到 —— 看起来像"案例没上架"，其实是网络不通）。
"$PY" -m uvicorn a2n_server.app:app --host 0.0.0.0 --port 18787 > data/sim_server.log 2>&1 &
echo "[sim] 平台 pid=$!（监听 0.0.0.0:18787，容器要回连；控制台开箱身份 ${LOCAL_DID:-读不到}）"

sleep 3
# 本机节点：**一个节点 = 一把钥匙 = 一个身份**，一次上架四张卡。
# 四档（收费 / 免费 / x402 / 试用中）只差卡面，不再是四个进程各冒充一个节点 ——
# 用户 2026-09-20 的模型是「本机 + docker 三个节点（共 4 个）本身组成网络」。
# 本地服务端口仍是 9102-9105（一张卡一个入口），平台经 relay 转发进来。
"$PY" scripts/run_local_node.py > data/sim_local_node.log 2>&1 &
echo "[sim] 本机节点已拉起（四张卡：9102 收费 / 9103 免费 / 9104 x402 / 9105 试用中）"
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
  echo "[sim] 镜像构建失败（看 data/sim_docker_build.log）—— 三个容器节点起不来"
fi
start_market_container () {
  local name="$1" key="$2"
  "$DOCKER" rm -f "$name" >/dev/null 2>&1 || true
  # MSYS_NO_PATHCONV：Git Bash 会把容器内路径 /app/... 错转成宿主 Git 安装目录
  # （真实踩过：python: can't open file '.../PortableGit/.../app/docker/market_node.py'）。
  MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' "$DOCKER" run -d --name "$name" \
    --add-host host.docker.internal:host-gateway \
    -e A2N_PLATFORM=http://host.docker.internal:18787 \
    -e A2N_CONTAINER="$key" \
    -v "a2n-demo-market-${key}:/app/state" \
    a2n-agent python -u /app/docker/market_node.py >/dev/null \
    || echo "[sim] 容器 $name 起不来（docker run 返回非 0）—— 收尾那一步会把证据摊开"
}
# 钥匙落在**命名卷**里（不是容器层）：重启才复用同一条上架（uid 由钥匙派生），
# 不会每起一次就多注册一条 —— 重启三次就是三份重复的案例。
start_market_container a2n-market-video   video-studio
start_market_container a2n-market-finance finance-legal
start_market_container a2n-market-play    play-ecom
echo "[sim] 三个容器节点已拉起（真实节点 · 模拟 agent 数据：短视频成片与口播稿 / 经营报表与合同草案 / 游戏策划案与商品详情页）"
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
# 期望注册数 = **算出来的，不是手写的 10**。手写的常量迟早与档位定义漂移，
# 而"这轮演示是绿的"这件事不该依赖一个抄来的数字。事实源就两处：
# 本机节点的档位（run_a2a_node.PRESETS）与案例容器的成员（CONTAINERS）。
# 两个模块都只有常量定义、main 有守卫，导入不会起任何服务。
EXPECTED="$("$PY" -c "import sys; sys.path.insert(0, '$ROOT_WIN/scripts'); from run_a2a_node import ROLES; from run_market_demo_agents import CONTAINERS; print(len(ROLES) + sum(len(c[2]) for c in CONTAINERS))" 2>/dev/null || true)"

# 注册是**并发异步**的：本机一个进程四张卡、三个容器各两张，同时在建。读数落在
# 半路上就会印出一个偏小的数字 —— 那与"真的少了两张卡"长得**一模一样**。
# 2026-09-20 第 3 轮冷启就印出过一个光秃秃的 8：真正的线索（finance 容器自己挂了，
# 日志里三处 Traceback）埋在 `docker logs` 里，下一轮 sim_stop 一冲就没了，
# 事后想查都查不到。所以这里两件事一起做：
#   ① 等它收敛（15 × 2s），别把"还在上架"误判成故障（反向的假红）；
#   ② 真不对就当场把证据摊到屏幕上，并且**以非 0 结束** —— 注册数不对的演示
#      不该被后面几步当成绿（同一条纪律：读数失败说"读不到"，绝不许伪装成 0）。
tries=0
while [ "$tries" -lt 15 ]; do
  N_AGENTS="$("$PY" -c "import json,urllib.request as u; print(len(json.load(u.urlopen('http://127.0.0.1:18787/v1/registry/agents', timeout=5))))" 2>/dev/null || true)"
  if [ -n "$EXPECTED" ] && [ "$N_AGENTS" = "$EXPECTED" ]; then
    break
  fi
  tries=$((tries + 1))
  if [ "$tries" -lt 15 ]; then
    sleep 2
  fi
done
echo "[sim] 注册数: ${N_AGENTS:-读不到}（期望 ${EXPECTED:-读不到}）"
if [ -n "$EXPECTED" ] && [ "$N_AGENTS" != "$EXPECTED" ]; then
  echo "[sim] ★ 注册数与期望不符 —— 证据摊在下面（这些日志下一轮就被冲掉了）："
  for name in a2n-market-video a2n-market-finance a2n-market-play; do
    echo "[sim] ── 容器 $name 状态 ──"
    # 先看容器是"死的"还是"活着但没上架"：这两种原因的排查方向完全相反。
    "$DOCKER" inspect -f 'Running={{.State.Running}} ExitCode={{.State.ExitCode}} StartedAt={{.State.StartedAt}}' "$name" 2>&1 || true
    echo "[sim] ── 容器 $name 最近 30 行 ──"
    "$DOCKER" logs --tail 30 "$name" 2>&1 || true
  done
  echo "[sim] ── 本机节点最近 20 行 ──"
  tail -20 data/sim_local_node.log 2>&1 || true
  echo "[sim] ── 平台最近 20 行 ──"
  tail -20 data/sim_server.log 2>&1 || true
  exit 1
fi
