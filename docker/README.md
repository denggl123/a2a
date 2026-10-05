# 一个节点就是一套平台

镜像启动真实 `Daemon`，没有演示 Agent、中央平台注册或另一套数据库。

## 普通部署

配置 `docker/.env` 的 `A2N_STORAGE_KEY`，运行 `docker compose -f docker/compose.yaml --env-file docker/.env up -d --build`。原三个命名卷沿用。旧卷中的供给和身份会恢复；旧演示上游需要通过管理 API 改绑到真实服务，不会重新生成案例。

Docker 内部使用容器 DNS，宿主机端口用于公共接口检查。要连接真实外部电脑，应把 `A2N_PUBLIC_BASE` 换成双方都能访问的 HTTPS 地址。管理 API 始终回环，不暴露到公共代理。

## 实际多节点验收

`python scripts/docker_acceptance.py` 构建同一镜像，启动独立 A、B、C 三节点及测试用 HTTP Agent，通过节点管理接口上架，然后执行实际发现和交易请求。结果写入 `artifacts/docker-acceptance.json` 和 Markdown 报告。测试服务只在验收 Compose 中加载。

节点 A 只配置 B，B 只配置 C；C 的供给通过 B 的引荐逐级发现。每个节点即使没有商品也必须提供基础发现；样品、见证、任务中继独立配置。Docker 桥接不等于公网 NAT、跨运营商或长期容量验证。
