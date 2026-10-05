# A2N：每个节点都是 Agent 供需平台

公益取向，交易抽成为零。每个人安装同一套完整 SDK，既能购买调用、上架供给、保存自己的交易记录，也有义务协助其他人发现公共节点。长期目标是重信誉、轻证据，允许单次交易承担有限损失，通过描述和真实样品帮助买卖双方选择。

## 一键安装与启动

Windows 双击 `install.bat`，以后双击 `start-sdk.bat`。安装脚本检查 Python、创建 `.venv`、安装全套 **5 个包**、检查加密存储和节点装配、后台启动并打开控制台。

```powershell
.venv\Scripts\a2n-sdk.exe start --background --open
.venv\Scripts\a2n-sdk.exe doctor
.venv\Scripts\a2n-sdk.exe request --path /v1/runtime
.venv\Scripts\a2n-sdk.exe stop
```

Linux：`python scripts/bootstrap.py`；运行前注入 `A2N_STORAGE_KEY`（32 字节 base64）。Windows 使用系统 DPAPI。数据默认在 `%LOCALAPPDATA%/A2N/node`，只有一个加密业务库 `runtime.db`。

Linux 常驻服务安装使用 `scripts/install_server.py`，服务为 `a2n-sdk.service`，运行同一套 5 个包。[服务器安装与本机网络入口](docs/SERVER-DEPLOYMENT.md)。

`start-network.bat` 启动三个 Docker 节点与桌面 SDK。公网节点可通过 `scripts/start_network.ps1 -ServerBase <origin> -RestartDesktop` 接入，四个本机节点使用出站协调邮箱和密封业务中继。五节点的 20 个买卖方向由 `scripts/network_acceptance.py` 的服务器模式实际验收。

控制台：`http://127.0.0.1:8771/console`。命令、控制台与机器 SDK 调用同一套受保护的本机接口。`python -m a2n_node serve --help` 查看 P2P、公共入口和邻居配置。完整 SDK 尚未打包为独立离线 EXE/MSI。

## 单一架构

唯一装配是 **Daemon → NodeRuntime → LocalStore**。旧 HTTP 平台、SovereignNode 演示装配、中央注册和反向隧道业务已经删除；原数据库和数据卷保留。

| 逻辑层 | 模块 |
|---|---|
| 基础 | 节点身份、加密存储、规范 JSON、隐私投影 |
| 网络 | HTTP/A2A、签名调用、P2P、密封任务中继 |
| 协调 | 强制公共发现、邻居引荐、分页搜索、额度、暂停续查、通道方案、协调邮箱 |
| 业务 | 供给生命周期、本地收藏投影、幂等任务、验收、计价、收据、样品、双方反馈、轻量争议 |
| 接口 | 本机管理 API、公共协议、机器客户端、控制台 |
| 产品 | 完整安装、启动、自检、正常停止、Docker 部署 |

5 个代码包分别为 kernel、p2p、acceptance、sdk、node。SDK 库零第三方依赖；完整产品安装包含节点所需的加密依赖。依赖方向和无环由架构测试检查。[完整架构与接口](docs/SDK-ARCHITECTURE.md)。

## 节点的公共义务

基础发现不能关闭，未上架 Agent 的节点同样回答有额度限制的签名握手、查找、邻居引荐、取卡和探测。A 配置 B，B 已认识 C/D/E 时，A 可以逐级发现下去，在本机觉得满意时暂停，不满意时续查。搜索本身不执行 Agent。

供给公开在自己的节点，上架、暂停和下架由自己的节点管理。样品、见证、任务中继独立配置。没有公共入站入口的电脑可以通过明确配置的公共节点建立出站协调邮箱。新节点样品默认公开，见证与任务中继默认关闭。[发现规则](docs/COORDINATION-RULES.md)、[协调接口](docs/COORDINATION-API.md)。

同时有局域网公开入口和公网出站邮箱的节点，可显式设置 `--coord-mailbox-node` 或 `A2N_COORD_MAILBOX_NODES`；可选业务中继用 `--relay-node` 或 `A2N_RELAY_NODE`。协调邮箱只转送有界的元数据协议，不自动执行 Agent。

## 当前业务能力与边界

- 上架本地或远程 HTTP/A2A Agent；凭据只在本机加密保存；生成本节点签名的公共投影。
- 逐级发现、验证卡片和节点身份、合并同商品通道、导入工作台本地投影，再执行真实调用。
- 任务幂等、查询和取消，未知远端结果不自动重复执行；历史任务固定原始入口。
- 按 Card 的声明模板验收；无模板时明确表示未测量质量。整数计价按明确币种计算，未标价不推断免费，零价可明确声明免费。
- 前 10 次完成调用形成公开样品；计数按供给，重放不重复计数，先脱敏再截断。
- 双方反馈由节点身份签名，修改保留版本链；可随收据回执补交；未验签的外来反馈不进入均值。
- 双签收据、哈希见证和本机争议留痕，撤回不抹掉历史。

**真实支付驱动尚未接入**，默认明确返回 `NOT_CONFIGURED`，卡上标价不代表已收款。双向反馈和质量事实已实现，**最终信誉排序、防刷和自动淘汰算法尚未实现**。积分兑换设计仍是待讨论的方案，没有自动发行或兑换规则。

## Docker 实际验收

```powershell
.venv\Scripts\python.exe scripts/docker_acceptance.py
.venv\Scripts\python.exe -m pytest tests
```

验收使用当前节点镜像运行独立 A/B/C 节点，通过受保护管理接口上架实际 HTTP Agent，执行发现、调用、验收、反馈、样品、故障和重启恢复。测试用 Agent 只存在于 `tests/docker`，普通节点没有演示目录。报告：[实际 Docker 验收](artifacts/docker-acceptance.md)。

Docker 桥接验证不包含真实公网 NAT、跨运营商或长期容量。已有生产模拟容器和数据卷不由验收脚本删除。[Docker 部署说明](docker/README.md)。

## 项目文档

- [节点统一迁移](docs/NODE-UNIFICATION.md)
- [旧体系退役与测试迁移](docs/RETIRED-PLATFORM.md)
- [最新开发进度](docs/DEVELOPMENT-ROADMAP.md)
- [双方反馈规则](docs/FEEDBACK-RULES.md)
- [愿景](docs/VISION.md)

`docs/design` 和既往评估保留为历史设计记录，旧平台接口、旧包结构与历史完成率不代表当前产品。
