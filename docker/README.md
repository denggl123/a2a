# 用 Docker 跑 A2N 节点（主机 ↔ 容器互通）

一句话：**容器不映射任何端口，全靠出站连接 + 反向隧道。**
这正是"家里电脑没有公网 IP 也能被发现和调用"的实证——Docker 容器在这里
就相当于一台 NAT 后面的机器。

## 架构

```
主机（平台）                          容器（agent）
uvicorn 0.0.0.0:8138  ←── 出站注册/心跳/取活/回包 ──  a2n-docker-ocr
        │                                            ├ 本地服务 127.0.0.1:8787（容器内）
        └── relay 入口 → 隧道 → 容器内本地服务 ────────┘
                    （外部打不进容器，容器也不暴露端口）
```

容器通过 `host.docker.internal` 访问主机平台（Docker Desktop 内置；
Linux 需 `--add-host host.docker.internal:host-gateway`）。

## 步骤

```bash
# 1) 主机起平台（必须 0.0.0.0，否则容器连不进来）
A2N_DB=data/net.db uvicorn a2n_server.app:app --host 0.0.0.0 --port 8138

# 2) 构建镜像
docker build -f docker/Dockerfile -t a2n-agent .

# 3) 起几个节点（注意：没有 -p，零端口映射）
docker run -d --name a2n-ocr --add-host host.docker.internal:host-gateway \
  -e A2N_PLATFORM=http://host.docker.internal:8138 \
  -e A2N_SKILL=ocr-pro -e A2N_NAME=docker-ocr \
  -e A2N_PRINCIPAL=acct:d-ocr -e A2N_GPU=4090 -e A2N_REGION=cn-docker-a \
  a2n-agent

# 4) 主机侧调用（发现 → 配对 → 门禁调用 → 对账出账）
python scripts/docker_demo.py http://127.0.0.1:8138 ocr-pro,translate,render
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `A2N_PLATFORM` | `http://host.docker.internal:8000` | 平台地址 |
| `A2N_NAME` / `A2N_SKILL` | `docker-agent` / `ocr-pro` | 节点名与能力 |
| `A2N_PRINCIPAL` | `acct:docker` | 主体 |
| `A2N_GPU` / `A2N_VRAM_GB` / `A2N_REGION` | `4090` / `24` / `cn-docker` | 参与多维发现 |
| `A2N_MAX_LATENCY_MS` / `A2N_PRICE_FEN` | `3000` / `3` | SLA 与提示价 |
| `A2N_LOCAL_PORT` | `8787` | 容器内本地服务端口 |

## 实测结果（已验证）

- 3 个容器 agent 注册成功，`docker ps` 的 **PORTS 列为空**（零端口暴露）；
- 主机按 `region` 多维筛选找到它们（cn-docker-a/b/c）；
- 主机经门禁调用，返回里的 `hostname` 是**容器 ID**（如 `84c18c1e3055`）——
  证明活确实是在容器里干的，不是主机自己；
- 调用链路：平台 relay 入口 → 反向隧道 → 容器内 `127.0.0.1:8787`；
- 每笔调用后走一期记账：双向上报 → 对账 RECONCILED → 出账。

## 停止

```bash
docker rm -f a2n-ocr a2n-trans a2n-render
```

## 不用 Docker 也可以

同样的节点程序可以直接用主机 Python 起多个进程（每个进程 = 一个 agent）：

```bash
A2N_PLATFORM=http://127.0.0.1:8138 A2N_SKILL=ocr-pro A2N_NAME=host-ocr \
A2N_PRINCIPAL=acct:p-ocr A2N_LOCAL_PORT=8781 python -u docker/agent_node.py
```
