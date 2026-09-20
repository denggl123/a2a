# 用 Docker 跑 A2N 节点（主机 ↔ 容器互通）

一句话：**容器不映射任何端口，全靠出站连接 + 反向隧道。**
这正是"家里电脑没有公网 IP 也能被发现和调用"的实证——Docker 容器在这里
就相当于一台 NAT 后面的机器。

## 架构

```
主机（平台）                          容器（agent）
uvicorn 0.0.0.0:8000  ←── 出站注册/心跳/取活/回包 ──  a2n-market-video
        │                                            ├ 本地服务 127.0.0.1:8787（容器内，第 1 份）
        └── relay 入口 → 隧道 → 容器内本地服务 ─────────┴ 本地服务 127.0.0.1:8788（容器内，第 2 份）
                    （外部打不进容器，容器也不暴露端口）
```

容器通过 `host.docker.internal` 访问主机平台（Docker Desktop 内置；
Linux 需 `--add-host host.docker.internal:host-gateway`）。
平台**必须监听 0.0.0.0**：只绑 127.0.0.1 的话容器连不进来，症状是容器日志里
一串连接被拒、而控制台上看着像"案例没上架"。

## 两类容器节点

**一台容器 = 一台机器 = 一个身份。** 容器扮演的是**公网上的另一台机器**（用户
2026-09-20 的口径：「本机 + docker 3 个节点（共 4 个）本身可以组成网络」「这 4 个
节点只是测试，真正好了会丢到外网，你只是用 docker 模拟公网环境」）。所以每个容器
一把钥匙（`/app/state/node.key.json`，挂命名卷），**上架主体就是它自己的 did** ——
不另造 `acct:alice` 这类账号，「谁在卖」只有一个答案。

| 脚本 | 是谁 | 最后一跳 |
|---|---|---|
| `docker/agent_node.py` | 单技能 OCR 节点（`A2N_SKILL=ocr-pro`），跨主机互通的最小样例 | **通**：返回带容器 hostname 的结果 |
| `docker/market_node.py` | 「找 Agent」里的**行业案例**（短视频 / 财务 / 法务 / 游戏 / 电商），一台容器一个身份、两份供给 | **真的连不上**：去连一台不存在的上游 agent，真实错误原样上报 |

案例那一路的定位要说清：**控制台 → 平台 → relay 入口 → 反向隧道 → 容器内本地服务**
整段是真生产链路（注册、心跳、地址投影、门禁、拨号一处不少）。断哪一跳？**断在容器
转发给自己那台 agent** —— 不是发现不到、不是对方节点掉线、也不是连不上容器。
所以它验的不是"调得通"，而是**断在哪一跳、有没有如实说**：失败原因是那次真实
连接尝试的结果（`URLError: [WinError 10061] …`），不是预先写好的文案，
更不许被换成 `upstream 500` 这种把三种情况糊成一种的通用噪声。

### 怎么自己造一张"卡在架但调不通"

上架**不需要**节点活着：`Registry.register` 只做卡形状校验 + 本地验签，不检查
`url` 通不通（平台模式 `require_endpoint=False`，`url` 为空都能上架）。
所以只要有一份签好名的卡，谁都能把它摆上架 —— 它指向的地址是死是活与"能不能
被发现、能不能被调用"无关。容器这一路演示的就是这个：**卡是真的、对方节点是活的、
请求也送到了**，只有它自己那台 agent 不在。

## 常规用法：跟着演示环境起来

```bash
bash scripts/sim_start.sh fresh     # 平台（0.0.0.0:8000）+ 本机节点 + 三个案例容器
```

`sim_start.sh` 会自己 `docker build` 并起这三个容器：

| 容器名 | 案例 |
|---|---|
| `a2n-market-video` | 短视频成片包（CNY+USDC 两条挂牌）· 短视频口播稿（免费） |
| `a2n-market-finance` | 经营报表 · 合同草案（都挂 CNY+USDC） |
| `a2n-market-play` | 游戏策划案 · 商品详情页（都免费） |

停止由 `python scripts/sim_stop.py` 一并做掉（`docker rm -f`）。案例容器一个
主机端口都不占，netstat 看不见它们，所以必须单独清。

## 手动起（不用 sim_start.sh）

```bash
# 1) 主机起平台（必须 0.0.0.0，否则容器连不进来）
A2N_DB=data/net.db uvicorn a2n_server.app:app --host 0.0.0.0 --port 8000

# 2) 构建镜像
docker build -f docker/Dockerfile -t a2n-agent .

# 3) 起一个案例容器（注意：没有 -p，零端口映射；主体默认就是它自己的 did）
docker run -d --name a2n-market-video --add-host host.docker.internal:host-gateway \
  -e A2N_PLATFORM=http://host.docker.internal:8000 \
  -e A2N_CONTAINER=video-studio \
  -v a2n-demo-market-video-studio:/app/state \
  a2n-agent python -u /app/docker/market_node.py

# 或者起那个**可调用**的单技能 OCR 节点
docker run -d --name a2n-ocr --add-host host.docker.internal:host-gateway \
  -e A2N_PLATFORM=http://host.docker.internal:8000 \
  -e A2N_SKILL=ocr-pro -e A2N_NAME=docker-ocr \
  -e A2N_REGION=cn-docker-a \
  a2n-agent

# 4) 主机侧调用（发现 → 配对 → 门禁调用 → 对账出账）
python scripts/docker_demo.py http://127.0.0.1:8000 ocr-pro,translate,render
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `A2N_PLATFORM` | `http://host.docker.internal:8000` | 平台地址 |
| `A2N_CONTAINER` | — | 案例容器要起哪一组（`video-studio` / `finance-legal` / `play-ecom`） |
| `A2N_PRINCIPAL` | **空 = 本容器节点自己的 did** | 覆盖上架主体；留空即"一个节点一个身份"（只有测试造两个主体时才给） |
| `A2N_DEAD_AGENT` | `http://127.0.0.1:9/invoke` | 案例如卡的"上游 agent"地址 —— 本地没人听，所以转发那一跳真的连不上 |
| `A2N_PORT_BASE` | `8787` | 容器内本地服务起始端口（两张卡各占一个） |
| `A2N_STATE_DIR` | `/app/state` | 钥匙落盘目录，**挂命名卷**进去 |
| `A2N_VISIBILITY` | `public` | 可见范围 |
| `A2N_NAME` / `A2N_SKILL` | `docker-agent` / `ocr-pro` | 仅单技能节点用 |
| `A2N_GPU` / `A2N_VRAM_GB` / `A2N_REGION` | `4090` / `24` / `cn-docker` | 参与多维发现 |
| `A2N_MAX_LATENCY_MS` / `A2N_PRICE_FEN` | `3000` / `3` | SLA 与提示价 |
| `A2N_LOCAL_PORT` | `8787` | 仅单技能节点用 |

## 两个踩过的坑

**一、镜像必须照抄仓库的目录形状**，不能把包铺平到 `/app` 下。
`a2n_store/db.py` 的默认库路径是 `Path(__file__).resolve().parents[4] / "data"`：
铺平之后 `parents[4]` 直接越界 `IndexError`，三个容器全部 `Exited(1)`，
而控制台上只看到"案例没上架"——很像注册挂了，其实是 import 就炸了。
（2026-09-19 真踩过。）

**二、钥匙要落盘在卷里**，不能每次启动现生成。uid 由 **did + 卡名**派生，换了钥匙
= 换了 did ⇒ 换了 uid ⇒ 平台上多出一条**新的**上架，而不是更新原来那条；重启三次，
发现页上就是三份重复的案例。**一台容器一把钥匙**（不是一张卡一把）：本来就是一个
节点一个身份，两张卡同签、同主体。

**三、容器连自己那台 agent 必须绕开代理**。上游 agent 就在本机，若走 HTTP 代理会
拿到一句 `502 Bad Gateway` —— 那是代理的话，不是"连不上 agent"的事实（本机常驻
代理拦 `127.0.0.1`，2026-09-20 真踩过：失败原因显示成 502，看着像网关问题）。
`market_node._agent_behind` 用 `ProxyHandler({})` 显式直连。

## 实测结果（已验证）

- 3 个案例容器注册成功（共 6 份供给），`docker ps` 的 **PORTS 列为空**（零端口暴露）；
- 控制台「找 Agent」里与**本机节点**的四张卡并列可见，卡自证 `signed`、`reachable=True`；
- 调它一笔（免费档，门禁放行）→ 任务 **FAILED**，且失败原因是容器**那次真实转发尝试**的结果
  （`转发到上游 agent（http://127.0.0.1:9/invoke）失败：URLError: [Errno 111] Connection refused…`
  —— 不是一句预先写好的文案，也不是 `upstream 500`），
  供给方「他人调用记录」里照记这一笔（失败也记，不许只记成功的）；
- 单技能 `agent_node.py` 那一路仍可调通：返回里的 `hostname` 是**容器 ID**
  （如 `84c18c1e3055`）—— 证明活确实是在容器里干的，不是主机自己。

## 停止

```bash
docker rm -f a2n-market-video a2n-market-finance a2n-market-play
# 命名卷留着 → 下次起来复用同一条上架；想彻底清就再加 -v
```

## 不用 Docker 也可以

同样的节点程序可以直接用主机 Python 起多个进程：

```bash
# 单技能节点（主体默认就是它自己的 did，不用给）
A2N_PLATFORM=http://127.0.0.1:8000 A2N_SKILL=ocr-pro A2N_NAME=host-ocr \
A2N_PORT_BASE=8781 python -u docker/market_node.py

# 行业案例（主机版，走 scripts 里那份老脚本 = 最后一跳是**通的**）
python scripts/run_market_demo_agents.py --base http://127.0.0.1:8000 --visibility public
```
