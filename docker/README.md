# 一个节点就是一套平台

镜像启动同一真实 `Daemon` 和加密业务库。HTTP Agent 和测试链是辅助服务，不是额外的 A2N 节点。总体状态见 [项目状态](../docs/PROJECT-STATUS.md)，工具分工见 [脚本入口](../scripts/README.md)。

## 当前本机四节点

Windows 完整 SDK 管理入口 8771，公共入口 18885；Docker `node-a/b/c` 公共入口 18881/18882/18883，管理接口保留在各容器回环。`docker/acceptance.yaml` 是三节点基础配置，`docker/network.yaml` 是连接 Windows/LAN/可选远端节点的覆盖配置。这些节点拥有实际身份和历史数据卷，不能当作每次测试后删除的临时目录。

初次构建专用环境：`python scripts/docker_acceptance.py`；连接 Windows：根目录 `start-network.bat`。再次启动已有网络使用原 `.tmp/network.env`，保留存储保护密钥：

```powershell
docker compose -f docker/acceptance.yaml -f docker/network.yaml --env-file .tmp/network.env up -d
```

该环境文件含存储秘密，不进入版本库。只开容器并不等于已经升级源码；当前镜像和四节点运行状态可用 `python scripts/project_audit.py --installed` 核对。

### 测试 Agent 和执行镜像

`agents` 是验收用 HTTP 服务（18884），`business-agent` 是实际文本检查 Agent（18910）。它们使用当前 `a2n-node-test` 镜像、只读代码挂载，不保存业务钱包或账本。文本 Agent 已有 Compose 配置：

```powershell
docker compose -f docker/acceptance.yaml -f docker/network.yaml --env-file .tmp/network.env --profile business up -d --no-deps agents business-agent
```

既有手动创建、同名文本容器首次迁到 Compose 时，须核验所属标签，再仅替换该容器；节点数据卷保留。升级节点镜像后应同步重建这两个测试服务，避免运行容器引用不存在的历史镜像。已安装工作流和独立评审还可能使用固定 SHA 的 `a2n-utilities` 镜像，它即使没有常驻容器也仍是有效依赖。

测试链与 facilitator 见 [本地测试支付](../docs/LOCAL-PAYMENT-TESTING.md)，两者持久卷必须配套保留。测试服务和本地测试币不证明真实用户认可或主网支付可用。

## 普通部署

`docker/compose.yaml` 是使用同一节点镜像的另一份部署模板，节点命名和端口不同；它不是第二套业务实现，也不是当前四节点的启动入口。配置 `docker/.env` 的 `A2N_STORAGE_KEY`，运行 `docker compose -f docker/compose.yaml --env-file docker/.env up -d --build`。原三个命名卷沿用；既有身份和供给由当前节点恢复。

Docker 内部使用容器 DNS，宿主机端口用于公共接口检查。要连接真实外部电脑，应把 `A2N_PUBLIC_BASE` 换成双方都能访问的 HTTPS 地址。管理 API 始终回环，不暴露到公共代理。

## 实际多节点验收

`python scripts/docker_acceptance.py` 构建同一镜像，启动独立 A、B、C 三节点及测试用 HTTP Agent，通过节点管理接口上架，然后执行实际发现和交易请求。结果写入 `artifacts/docker-acceptance.json` 和 Markdown 报告。测试服务只在验收 Compose 中加载。

节点 A 只配置 B，B 只配置 C；C 的供给通过 B 的引荐逐级发现。每个节点即使没有商品也必须提供基础发现和既有公开样品；见证、任务中继独立配置。Docker 桥接不等于公网 NAT、跨运营商或长期容量验证。

清理前须同时检查容器、签名工作流和评审镜像依赖；身份、账本、加密备份和恢复资料不按“未挂载”判为垃圾。历史报告保留原日期，实际已安装节点验收产生真实业务事实，不能充当真人校准数据。
