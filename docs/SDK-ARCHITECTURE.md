> **2026-10-08 实现更新**：[支付协调层](PAYMENT-COORDINATION.md) 已接通双边签名计划、统一付款订单、收费 Agent 合同、原生币转账、x402 与独立退款。SDK 保持零依赖；真实私有 EVM 已验证付款和退款，真实外部资金与新设备试点尚未验证。既往设计稿和下文历史进度请结合本次实现记录阅读。

# 完整桌面 SDK 与模块边界

更新：2026-10-08。这里的 SDK 指安装在电脑上的完整 A2N 节点产品。

## 1. 产品入口

Windows 双击仓库根目录 `install.bat`：

1. 检查 Python 3.11+；缺少时通过 Windows App Installer 的 winget 安装用户级 Python 3.13。
2. 建立 `.venv`，安装第三方依赖与全部 5 个本地包。
3. 运行完整产品自检：包导入、系统凭据保护、临时节点、本机 HTTP、协调签名。
4. 后台启动节点，打开 `http://127.0.0.1:8771/console`。

以后双击 `start-sdk.bat`。命令入口：

```powershell
.venv\Scripts\a2n-sdk.exe start --background --open
.venv\Scripts\a2n-sdk.exe doctor
```

`a2n-node serve`、`python scripts/bootstrap.py` 和旧安装脚本继续兼容；旧安装脚本统一转交 bootstrap。`--check` 做完整自检，`--dry-run` 不安装，`--launch` 安装后启动。

上述是源码安装入口，需要下载依赖。独立完整程序 `artifacts/A2N.exe` 已内置运行时与全部五包，双击提供安装和离线恢复页面，并可注册当前用户登录启动；系统拒绝修改已有计划任务时使用当前用户登录入口。尚无系统托盘与 MSI。Linux/容器继续通过 `A2N_STORAGE_KEY` 配置存储保护。

默认数据目录 `%LOCALAPPDATA%\A2N\node`，后台日志 `node.log`。升级保留身份、供给、账户、调用、样品、反馈及本地凭证。启动器核对端口对应的节点目录，避免打开另一份实例。

同一目录的桌面启动由 `desktop-launcher.lock` 选出一个启动器，计划任务、登录启动和安装器重复启动时等待原节点就绪后正常退出。默认后台启动统一经过 `a2n_node.desktop`，有无 `network.json` 均遵守此规则。`runtime.lock` 仍由 Daemon 持有到完整停机，保护加密存储及离线恢复；启动器不再用临时获取、释放存储锁判断可以启动。旧节点停机后先释放启动器锁，再核验并接管已安装的新程序。等待 30 秒仍没有就绪的本机入口，记录日志并返回非零状态；真实启动错误和端口归属错误继续报错。兼容计划任务脚本只重试非正常退出，正常复用或主动停机后退出。

便携安装可在启动目录保存 `.a2n-local.json`，内容为 `{"home":"绝对节点目录"}`；`A2N_HOME` 环境变量优先。控制台启动、CLI 与 Windows 计划任务在同一启动目录读取同一配置。当前四节点环境使用仓库 `data/desktop-sdk`，原 SDK 身份和 33 条历史调用已迁入，并保留迁移前的加密备份。桌面网络参数存放在节点目录的 `network.json`，普通 SDK 重启继续使用这些参数。

## 2. 六个逻辑层级

这些模块在一个常驻进程中装配，通过端口衔接。

| 层级 | 职责 | 当前模块 |
|---|---|---|
| 基础层 | 身份、加密、存储、规范 JSON、脱敏 | `a2n_p2p.Identity`、`a2n_node.protection`、`a2n_sdk.storage`、`serialization`、`privacy` |
| 网络层 | 有界连接、传输、P2P 观察、直连与密封任务中继 | `coord_network`、`p2p_service`、`peer_transport`、`relay_transport`、`relay_service`、`relay_provider`、`adapters` |
| 协调层 | 公共握手、邻居维护、分页引荐、搜索会话、额度、候选合并、通道观测与方案；支付条件协商、方法选择、结算编排与恢复 | `coordination`、`coordination_service`、`coord_identity`、`coord_service`、`coord_neighbors`、`coord_mailbox`、`payment_coordination`、`payments`、`points_coordination`；Node `payment_service`、`points_payment` |
| 业务层 | 供给、收藏、固定条件、事实、样品、反馈、体验、信誉、风险、本机策略及协商；决定是否发起结算 | `runtime`、`calls`、`pipeline`、`trade_facts`、`contracts`、`trials`、`feedback`、`experience`、`reputation`、`policies`、`resolutions`、`reconnect`、`agent_packages`、`assets`；Node `trade_service` |
| 独立支付子模块 | 各方式自己的资金或积分规则；由协调层调用，不决定业务资格 | SDK `points`；Node `points_service`、`evm_payment`、`x402/` |
| 接口层 | 本机保护、公共协议翻译、控制台 | `local_api`、`coordination_api`、`web/runtime.html`、`web/coordination.js`、`management` |
| 产品装配层 | 全套安装、常驻、自检、生命周期、备份恢复及升级 | `Daemon`、`product_cli`、`desktop`、`autostart`、`backups`、`upgrades`、`scripts/desktop_launcher.py`、`scripts/bootstrap.py` |

5 个保留包的依赖层级（kernel=0，p2p/acceptance=1，sdk=2，node=3）由 `tests/test_package_layers.py` 校验。上表按运行职责划分；SDK 包内的协调契约与应用服务保持零第三方依赖，身份与网络实现由节点注入。

2026-10-08 实现：原生币、x402 与积分均是支付协调调用的独立子模块，支付子模块是协调层的下属实现边界，不增加一个顶层产品。`TradeService` 已承担免费资格、接单及调用；前十次样品跳过全部结算，不创建零金额订单。积分的服务发行增加供应方持有的自家积分，主动加分增加指定使用方持有的供应方积分。`PointsPayment` 处理自愿选路、全体预留、持久提交及恢复；`PointsBook` 仅记录发行方自己的账本。规则、接口及实现限制见 [积分模块](POINTS.md)。

产品主路径：`a2n-sdk start → Daemon → NodeRuntime`，数据在加密的 `runtime.db`。旧 `SovereignNode`、中央 HTTP 平台、注册与反向隧道装配已删除；原数据文件保留，中央财务余额不解释为节点余额。

## 3. 协调实现

### 本机端口

`CoordinationService` 实现 `CoordinationPort`；`CandidatePolicy` 实现 `LocalCandidatePolicy`；`CoordinationNetwork` 实现 `CoordinationNetworkPort`。

本机 HTTP 路径：

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/v1/coord/searches` | 创建搜索 |
| GET | `/v1/coord/searches/{id}` | 状态、控制版本与额度 |
| GET | `/v1/coord/searches/{id}/candidates` | 按结果版本分页 |
| POST | `/v1/coord/searches/{id}/pause` | 停止新派发 |
| POST | `/v1/coord/searches/{id}/resume` | 保留候选和队列，追加一轮预算 |
| POST | `/v1/coord/searches/{id}/cancel` | 终止会话 |
| POST | `/v1/coord/searches/{id}/evaluate` | 本机满意策略评估 |
| POST | `/v1/coord/searches/{id}/route-plan` | 通道方案，可选只读探测 |

修改请求要求 `Idempotency-Key`；除创建外还要求 `If-Match: "控制版本"`。重复命令优先重放；不同内容复用同一键报 409。进度更新和结果更新分别计版本，不挤占控制版本。所有本机接口沿用令牌、Host 与 Origin 保护。

默认满意策略只接受已验签候选，支持提供方与版本条件、`preferences.min_candidates`（默认 3）。未知必要条件或策略标识进入需要本机输入的暂停状态。控制台续查提高目标候选数；API 的 resume 可提交新的 `preferences`。私有条件与精确预算留在本机。

### 公共协议

六个签名操作位于 `/public/v1/coord/{hello,find,card,routes,probe,forward}`。握手验证节点身份和 nonce；引荐验证介绍者与目标节点声明；取卡核对签名、商品键与哈希。

节点后台只维护明确配置的公共邻居，每 45 秒刷新签名声明，每批最多 3 个、最多 32 个入口。维护只发 HELLO，不替用户搜索、不遍历引荐、不调用 Agent。因此 B 配置了 C/D/E 时，A 可以从 B 取得持续更新的引荐。

FIND 每页最多 10 项、引荐最多 8 项；商品与引荐交错提供。搜索按来源轮转，商品键统一为 `(provider_did, service_id)`，同商品的 Card、通道与来源分别保留。供给投影中的 `service_id` 为实际商品标识。

出站协调邮箱使用 `/public/v1/coord/mailbox/{register,poll,reply}`。内网节点连接一个配置过的公共节点，持有短租约，回答发给自己的签名协调请求。父节点保留目标原始应答签名。当前转送限定一个明确邻居，不接受嵌套 FORWARD；协调邮箱和密封任务邮箱分别管理。

### 持久化与资源

- 搜索、队列、候选、命令结果、额度进入加密本地库。重启把中断搜索恢复为暂停，保留预留消耗，并重新排入中断的只读操作。
- 暂停允许在途请求完成；在途请求或探测尚未结束时，resume 返回 BUSY，避免重置其额度。
- 每次网络操作先预留请求与字节额度；完整应答后返还未使用字节，失败和中断保留预留。探测按每通道一次操作、最多 64 KiB 和 800 ms 保守预留。
- 自动续查要求单独的总预算，最多 64 轮。手动续查也服从已有总预算。
- 本机同时最多 4 次运行搜索，每会话串行访问远端；最大队列 512、会话 256、缓存卡 512。会话 TTL 1 小时，节点和通道声明 TTL 120 秒。
- 公共单报文最多 256 KiB，按身份每分钟最多 120 次；邮箱最多 128 个租约，每目标最多 8 个在途请求。这些是当前试点参数。
- 新协议使用固定 DNS 解析地址、验证 TLS、禁止重定向和系统代理，并检查私网地址。引荐不授予访问内网权限；局域网试点需显式 `--coord-allow-network CIDR`。旧目录和 UDP 发现保留兼容适配，其能力单独标记。
- 协调中的 UDP 兼容发现每次只询问一个直邻、读取一张卡，不广播扩散、不并发取卡；最多遍历 32 个已知直邻。兼容路径限制实际读取大小，并保守保留整笔字节预留，不用最终卡大小代替网络消耗。网络心跳与明确邻居维护独立于搜索额度。

搜索、读样品、探测、选路均不调用 Agent，不占用试用，不验收或结算。

## 4. 通道与调用衔接

`POST /v1/projections` 可提交 `search_id`、`key` 和可选 `route_id`。管理服务从本机会话取回已验签、未过期的通道，不接受客户端另造通道方案。导入仍是业务侧显式操作。

默认按本机可达性观测、延迟与直连偏好选路。SDK 的传输适配器消费这些选择；仅确认未连接到对端时可尝试同商品的已批准备用通道。身份失败、业务失败和超时不自动重试。

创建任务时持久化完整 Card 和实际入口。查询、取消、晚到收据核验继续使用这条入口；刷新收藏不迁移已经接受的任务。只读元数据探测产生 `control_reachable`，`task_reachable` 保持未知。

## 5. 公共职责与迁移

基础发现与免费交付样品始终开启。`POST /v1/public-services` 的 body 如 `{"services":{"witness":false,"task_relay":false}}`，分别配置可选服务；`discovery:false` 或 `samples:false` 返回 400，整次配置不生效。旧 `/v1/public-service` 总开关只控制见证和任务中继，不能关闭样品。启动时将旧样品关闭设置规范为开启；下架或卸载供给仍可按原 service_id 读取历史样品。见证、任务中继及文件转送默认关闭；`blob_cache` 已接入独立有额度的加密文件转送，当前不提供持久缓存。

旧总开关继续作为可选服务的兼容入口；旧的显式关闭配置保留，但不能关闭基础协调。控制台显示独立开关。提供公共服务的机器仍需可访问入口，普通内网电脑可通过配置的公共节点建立出站协调邮箱。没有邻居时显示孤立状态。

## 6. 当前完成边界

已安装并接入产品：供给、收藏投影、签名调用、验收、双边收据、试用样品、反馈及版本链、轻量争议、协调发现与邮箱、通道方案、控制台、启动与自检。

旧金融与旧信誉包已经退役。币种、计量维度、整数计价迁入 SDK；通用业务入口 `/v1/trades/*` 已接入原生币、x402 和积分。默认不配置真实资金钱包，不启用积分服务政策或对他方积分的接受表。`GET /v1/quality` 只返回本机质量事实；外来未验签反馈不进入均值。双向反馈已经交换并提供事实摘要；多维信誉、防刷贡献上限与本机机会治理已有实现，默认影子运行。业务候选策略已接入协调层，硬条件、信誉支持量和新人机会在本机评估；真实业务校准仍需试点。

验证覆盖本机多节点的真实 HTTP 与签名协议、局部网络故障、暂停恢复、额度、身份失败和调用衔接。单文件程序的实际执行、签名升级、备份恢复和独立文件 NAT 邮箱已经隔离节点验收；真实跨公网、多种 NAT、长期在线容量与新设备图形安装仍需验证，不能用本机测试替代。

## 7. 唯一上架与机器接口

`POST /v1/bindings/http` 接受 `listed`（默认 true）；`/v1/publish` 和 `/v1/unpublish` 修改本节点公开状态，支持重复操作。`/v1/bindings/state` 暂停或恢复接单，`/v1/bindings/remove` 卸载并退出目录。下架拒绝新的公共调用，历史任务仍可由原买方查询。旧出版记录在启动时一次性转成 listed 元数据，原记录保存到 migration_archive。

机器客户端为 `a2n_sdk.NodeClient`（Client 是同一实现的别名），仅连接回环 HTTP。完整产品命令 `a2n-sdk request --home ... --port ... --method POST --path ... --body -` 从加密库取当前控制令牌；令牌每次启动更新。`a2n-sdk stop` 通过受保护 API 正常停止。公共代理放行公开协议、双签回执与密封中继，拒绝管理页面和 API。
