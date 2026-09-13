# A2N —— 只发行情、只刻章、不碰钱的 Agent 服务网络

本地可运行的最小闭环（S0-S2 垂直切片）。

## 启动

```bash
# 1. 装依赖
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt

# 2. 起服务
.venv/Scripts/python -m uvicorn a2n_server.app:app --port 8000

# 3. 灌演示数据（另一个终端）
.venv/Scripts/python scripts/seed.py

# 4. 打开管理台
#    http://127.0.0.1:8000/console
```

### 演示环境：一条命令起平台 + 三节点

三节点是**三档不一样的样本**（属地 / 价目 / 结算方式 / 延迟都不同），
不是三个克隆体——档位定义在 `scripts/run_a2a_node.py` 的 `PRESETS`：

| 端口 | 档位 | 属地 | 价目 | 结算方式 |
|---|---|---|---|---|
| 9102 | 华东·精算 OCR | cn-east-2 | ¥0.03/次 | 对等账户 + 直付渠道 |
| 9103 | 华北·公益 OCR | cn-north-1 | 免费 | 无（零准备可调） |
| 9104 | 新加坡·极速 OCR | ap-southeast-1 | 0.05 USDC/次 | 只收 x402 |

```bash
bash scripts/sim_start.sh fresh      # 清库冷启：平台 8000 + 三节点
python scripts/a2a_smoke.py          # 端到端冒烟（发现→免费→收费→直付→对等→x402）

# 命令行走一遍发现与调用（不写 Python）
python -m a2n_sdk discover --skill ocr-pro --limit 5
python -m a2n_sdk call --principal acct:alice --agent ag_xxx --skill ocr-pro --payload '{"text":"hi"}'
```

管理台的三层体检（改 `console.html` 后按顺序跑）：

```bash
node scripts/console_js_check.js        # ① 语法：<script> 块能否编译
node scripts/console_logic_check.js     # ② 纯逻辑：价格换算/能力判定/筛选（39 项）
node scripts/ui_check.js                # ③ 渲染：真浏览器打开 /console 断言（25 项，需 playwright-core）
```

## 两个入口

| 入口 | 给谁用 | 地址 |
|---|---|---|
| **管理台** | 人：观察我提供的 / 管理我调用的 | `/console` |
| **SDK** | 机器：agent 或程序调用 agent | `sdk/a2n_sdk` |

```python
from a2n_sdk import Node
node = Node(card, {"ocr-pro": handler}, principal="acct:bob")
node.serve()          # 注册 → 心跳 → 拉任务 → 执行 → 上报计量
```

## 三条红线（测试锁死）

```bash
.venv/Scripts/python -m pytest tests -q
```

1. **增发函数不存在** —— `mint` / `burn` 只允许被持牌方回调触发，静态扫描 + 运行时调用栈双重拦截
2. **总量恒等于托管** —— 积分总量 ≡ 托管余额，差一分即冻结提现
3. **账本 append-only** —— 数据库触发器禁止 UPDATE / DELETE，hash 链可检测任何篡改

其余红线测试：账本无余额字段、提示价不参与排序。

## 关键设计落点

| 设计 | 代码位置 |
|---|---|
| 只刻章不碰钱 | `adapters/custodian/` —— 唯一能对话外部资金的抽象，当前是 Mock |
| 账本不知道业务 | `domain/ledger/service.py` —— 只认 `ref_type` / `ref_id` |
| 分账规则可换且版本化 | `kernel/policy.py` —— `PolicyRef(key, version, params)` |
| 发现三级漏斗 | `domain/dispatch/service.py` —— 匹配 → 过滤 → 信誉排序 |
| 双向计量对账 | `domain/acceptance/service.py` —— 自报 vs 平台观测 |
| 凭证链 | `support/notary/service.py` —— 纯订阅者，挂了只影响盖章 |

## 存储

本地用 **SQLite**（`data/a2n.db`）保证开箱即跑；生产目标为 PostgreSQL，
切换点在 `a2n/db.py`（append-only 触发器在 PG 里用权限 + 规则实现）。

在线状态在本演示中用 `last_seen_at` 近似，生产应改用 Redis TTL。

## 连接模型：家宽电脑如何接单（v1.1 新增）

**A2N 不要求节点有公网入口。** 注册、心跳、取活、回传全部由节点出站发起：

| 模式 | 原理 | 适用 |
|---|---|---|
| `pull`（默认） | 节点长轮询取活（`GET /v1/nodes/{id}/tasks?wait=15`），有单立即返回 | 家宽 / 公司 NAT / CGNAT，零配置 |
| `wss` | 出站长连接，平台复用下发（规划中） | 同上，要求更低延迟 |
| `direct` / `relay` | 节点真有公网 URL（云主机或 frp/cloudflared），平台定期入站探测 | 低延迟场景 |

三条配套规则：

1. **NAT 判定平台替你做**：节点心跳自报本机网卡 IP，平台比对心跳来源 IP，
   回传 `nat: public | natted`。节点不需要知道自己的公网 IP。
2. **内网地址声明 direct 不认**：`127.0.0.1` / `192.168.x` 会被强制降级为 pull
   （`support/reachability.py::normalize_connection`），防"派了单却送不进去"。
3. **发现自由，派单受控**：不可达的节点照样能被搜到（那只是信息），
   但派单前校验会拦下（`discovery.assignable`），预算不会冻在永远不会执行的任务上。

### SDK 内嵌本地管理台

```bash
.venv/Scripts/python scripts/run_node.py
# 节点常驻 + 本地管理台 http://127.0.0.1:8770（只监听 127.0.0.1）
```

本地管理台展示：节点状态、连接处境（出口 IP vs 本机 IP → NAT 判定与解释）、
**本地观测**（我侧成功率/时延——平台不知道、也不该知道的那部分）、累计收益、最近任务。
数据 = 平台 API（账目、状态）+ 进程内统计（本地观测），零第三方依赖。

```python
from a2n_sdk import Node
node = Node(card, {"ocr-pro": handler}, principal="acct:bob")
node.serve(console=True)   # console=False 关闭本地管理台
```

## 传输阶梯 v1.2：p2p 穿透 / 反向长连接 / 中继转发（全部纳入协商）

核心事实：**平台有公网，"节点↔平台"永远只需要出站，不需要打洞。**
打洞只服务"两端都在 NAT 后"的场景，而 A2N v1 的调用拓扑里它不出现。
三条不可妥协的边界：

1. **打洞只做候选，永不做依赖**——对称 NAT/CGNAT 成功率低；SDK 已实现
   STUN 反射探测（RFC 5389 标准库实现），候选进入协商清单，但永不自动选中。
2. **数据可以走直连，证据必须走平台**——结果 hash、计量、分账证据仍回平台刻章。
3. **中继是唯一 100% 保证连通的通道**——业界约 8~20% 连接最终靠 TURN。

协商阶梯（ICE 思路：先列全候选，再选优；`GET /v1/nodes/{id}/transport`）：

| 通道 | 状态 | 实现 |
|---|---|---|
| `direct` | 可用 | 节点自报公网 URL + 平台入站探测 |
| `holepunch` | 候选枚举（实验） | STUN srflx 候选；平台仲裁打洞未实现 |
| `tunnel` | **可用** | 反向长连接：节点出站挂住，任务/调用实时推下来（生产 WSS，v1 长轮询下行同构） |
| `relay` | **可用** | 中继转发：平台公网入口 `/v1/relay/{agent_id}/*` → 隧道 → 节点本地 HTTP 服务 |
| `pull` | 可用（兜底） | 长轮询，永远可用 |

SDK 用法：

```python
node.serve(tunnel=True)                        # 反向长连接，任务实时下发
node.serve(local_agent=(9101, local_api))      # relay：本机服务可被公网调用，本机零暴露
```

中继转发演示（本机不开任何端口对外）：

```bash
curl -X POST http://127.0.0.1:8000/v1/relay/<agent_id>/echo \
     -H "Content-Type: application/json" -d '{"hello":"world"}'
# → 平台 → 隧道 → 节点本机 9101 → 回包原路返回
```

工程教训（已修复并写测试）：`relay` 路由曾是 `async def` 且同步等待节点回包
15s——事件循环被阻塞，全站长轮询一起超时。阻塞等待必须 `run_in_threadpool`。

## 分包结构 v1.3：23 个可独立发布的包

单体已拆分完毕，`a2n/` 与 `sdk/` 不再存在。目录：

```
packages/
  a2n-kernel/      L0  哈希链、Merkle、领域事件、策略版本化（零依赖）
  a2n-store/       L0  schema、连接、append-only 触发器、outbox
  a2n-ledger/      L1  账本：积分真相源
  a2n-custodian/   L1  持牌方适配器（唯一能碰钱的包）
  a2n-registry/    L2  注册/发现/KYA/可达性
  a2n-transport/   L2  传输阶梯：隧道、中继、协商
  a2n-reputation/  L2  信誉（唯一可进排序的第三方事实）
  a2n-dispatch/    L3  候选集与派单校验
  a2n-acceptance/  L3  验收与双向计量对账 + 争议（仲裁入口）
  a2n-settlement/  L3  分账指令 + 每日对账 + 仲裁退款执行
  a2n-wallet/      L3  提现与销毁
  a2n-task/        L3  任务状态机与编排（对外 A2A v1.0 视图）
  a2n-notary/      L4  凭证链
  a2n-market/      L4  行情投影
  a2n-consensus/   L4  epoch 批次、Merkle 根、SPV、见证与资金锚（v1.5）
  a2n-ap2/         L4  AP2 授权世界 ⇄ A2N 结算世界的翻译层（v1.5）
  a2n-p2p/         L0  DID 身份、对等发现、gossip、可注入签名器
  a2n-account/     L2  账户与支付方式（compatible_with 是支付能力交集的唯一实现）
  a2n-gateway/     L2  门禁唯一源 gate.resolve
  a2n-deal/        L3  双边记账（bilateral）
  a2n-node/        L4  自持节点：无服务器、无托管时的发现/调用/互证（v1.6）
  a2n-server/      L5  HTTP 装配 + wiring 接线 + 管理台
  a2n-sdk/         独立 零第三方依赖
```

安装与运行：

```bash
bash scripts/install_all.sh                       # 逐个 pip install -e --no-deps
.venv/Scripts/python -m uvicorn a2n_server.app:app --port 8000
.venv/Scripts/python -m pytest tests -q           # 132 passed
```

**纪律靠机器执行，不靠自觉**（`tests/test_architecture.py`）：

1. 只能 import 自己 `pyproject.toml` 里声明过的 `a2n-*` 包 —— 未声明即判失败；
2. 依赖图必须无环（函数级 import 也算环，只是它躲到了运行时）；
3. SDK 必须零依赖；
4. 只有 `a2n-custodian` 允许出现资金系统字样；
5. `a2n-kernel` 不许长出业务概念。

迁移时真实抓到的两处架构债：registry ↔ transport 环形依赖（改为依赖注入）、
kernel 反向依赖 store 写 outbox（改为事件总线 `set_sink()` 由装配层接线）。

## 网络层 a2n-p2p v1.4：没有中心服务器的按需发现（新增包）

第 17 个包，补齐"网络是底层"里唯一完全空白的一块。它只回答网络层四个问题中的三个：

| 问题 | 实现 |
|---|---|
| Q1 我是谁 | `Identity` ed25519，`did:a2n:ag_<公钥指纹>`，私钥永不出本机 |
| Q2 我怎么找到别人 | `P2PNode` 邻居发现：broadcast beacon / bootstrap / gossip 对等交换 |
| Q3 消息怎么到 | `Envelope` gossip：**去重 + TTL + 签名**三件套 |
| Q4 我们怎么共识 | 不在本包（a2n-consensus），共识需要知道账本 |

```bash
.venv/Scripts/python scripts/p2p_demo.py
```

演示 `A ── B ── C(ocr-pro)`：**A 只认识 B，从未连过 C，却能发现它。**
这就是"注册只是允许被发现、发现是按需的、没人同步全量目录"的底层实现。

```python
from a2n_p2p import Identity, P2PNode
net = P2PNode(Identity.generate(), port=9701,
              bootstrap=[("seed.example.com", 9701)]).start()
net.announce(["ocr-pro"])                 # 广播能力
offers = net.query("ocr-pro", timeout=2)  # 按需发现（可跨多跳）
```

### 四条边界（都在测试里锁死）

1. **大负载不走 gossip** —— 超过 60KB 直接拒绝，那是传输层隧道（tunnel/relay）的活。
2. **网络层不传递无法验证来源的东西** —— 查不到公钥即丢弃。
3. **TTL 不参与签名** —— 它是传输属性，每跳都变，中间节点无权代表发起者重签。
   放进签名域会让任何一次转发都失效，多跳网络直接瘫痪。防放大由接收侧 clamp 保证。
4. **TOFU 学到的公钥 ≠ 被担保** —— 网络层只能证明"消息出自持私钥者"，
   不能证明"这个人可信"。**信任归管理层信誉（D5）裁定**，网络层不越界。

### 实现中抓到的三个真 bug

1. **转发污染订阅者对象** —— 就地 `decay()` 改 TTL，订阅者持有的信封被改，
   事后验签必然失败。改为克隆后 decay。
2. **查询会话号对不上** —— 用新生成的 qid，而应答回填的是 envelope 的 msg_id。
   改用报文自身的 msg_id 作为会话号。
3. **多跳应答回不来** —— 响应者只认 `reply_to`（DID），不认识发起者就无法回信。
   P2P 中查询必须自带**回信地址**与**自报公钥**。

另外顺手修了架构测试自身的一个缺陷：正则 `a2n_[a-z]+` 匹配不到含数字的
`a2n_p2p`，会把它误判成未声明的包。

测试 65 个全绿（新增 21 个）。

## v1.5：把剩下的缺口一次补齐，并把所有模块衔接好

### 1. 共识层 `a2n-consensus`：锚定共识

没有中心服务器，"账本是对的"就不能由任何人单方面宣布。每个 epoch 把区间
账目压成一个 Merkle 根，**三把钥匙**集齐才定案：

| 钥匙 | 谁拿着 | 防谁 |
|---|---|---|
| 账本批次根（重算哈希链 + Merkle） | 节点 | 改账 |
| ≥2/3 见证权重签名 | 网络（WitnessSet，TOFU 只增不改） | 单点作恶 |
| 资金锚（托管余额声明签名） | 持牌方 | 凭空增发 |

任何一方单独都凑不齐三把钥匙。定案后自动向 P2P 网络广播 `ANCHOR`。
轻节点用 SPV 证明（O(log n)）即可验证"某条账目在某批已锚定的账里"，
不必同步全量 —— 家宽电脑参与核账的前提。

**测试抓到一个真 bug**：finalize 的"账本重算"最初只是重新收集存储的
hash 字段 —— 同义反复，篡改 delta 根本查不出来。已改为重算每条账目的
哈希链（delta 改 → chain_hash 不符；hash 字段改 → 断链）。

### 2. AP2 集成 `a2n-ap2`：翻译层，不是支付层

AP2 管点对点消费（授权/问责），A2N 管任务结算（验收/分账）。翻译层三条边界：
只翻译语义不碰钱；授权必须落地成**冻结**；结算凭证必须自带可验证证据。
三个扩展一一对应三个端点：

| 扩展 | 端点 | 语义 |
|---|---|---|
| 预算 | `POST /v1/ap2/budget` | Intent 授权（分）→ A2N 冻结预算（积分），1:1 无换算 |
| 凭证 | `POST /v1/ap2/receipt` | 结算 → 四件套证据（result_hash/双向计量/分账/公证章+epoch） |
| 问责 | `POST /v1/ap2/accountability` | 争议时的一页纸：自报 vs 观测 + 信誉快照 + 凭证 |

授权链（Intent→Cart→Payment）机器校验：引用闭合、主体一致、时效、
**双向金额锁死**（支付授权不得超购物车，购物车不得超授权）、可选签名验证。

### 3. A2A v1.0 状态机对齐 `a2n-task`

内部状态机显式化（`TRANSITIONS` 非法迁移直接拒绝），对外说 A2A 的话：
`GET /v1/tasks/{id}/a2a` 返回标准 Task 对象（submitted/working/completed/
failed/canceled + artifacts），A2N 特有信息全在 `metadata["x-a2n"]`。
新增 `cancel`（发起方取消，冻结原路退回）与 `fail`（节点侧失败，与
验收不通过的 REJECTED 分开记账）。

顺手修掉一个语义错误：`compute_amount` 的 `amount<=0` 兜底 1 积分已移除 ——
无可计费计量 = 计量缺失，判打回退款，绝不凭空造一笔"来源不明的 1 分钱"。

### 4. 仲裁工作台 `a2n-acceptance/dispute`

验收不通过 → 自动开争议单（system）；双方可申诉；仲裁员裁定
（uphold_reject / overturn_pay / partial）写入凭证链；退款由装配线转给
结算层**按原分账份额追回**（节点→服务费→激励池→作者池，宁记 shortfall
不透支）。管理台新增「仲裁」页可直接裁定。

### 5. 装配接线 `a2n-server/wiring.py`：所有"谁连谁"只写一次

```
events ──────────→ outbox            内核不碰存储
ledger ──────────→ consensus         共识要知道账本，但不 import 它（依赖注入）
custodian ───────→ consensus         资金锚读数来自持牌方
acceptance.failed → disputes        机器判不了的自动转人工
arbitration.resolved → settlement   裁定产出"该怎么退"，执行交给结算层
epoch.anchored ──→ p2p ANCHOR gossip 定案向网络广播
p2p offers ──────→ rosters          发现是一次网络行为，结果沉淀为本地市场列表
```

SDK 侧补齐：`sync_market()`（发现结果落本地市场文件）、`a2a_state()`
（标准 A2A 视图）、`cancel_task()`、`ap2_budget()/ap2_receipt()`。
管理台新增「仲裁 / 共识 / AP2」三个页签。

### 三层红线在新模块里的落点

- 共识层不创造事实：它只压缩与证明账本里已有的东西；
- AP2 层不碰钱：翻译进来的授权必须经 A2N 的冻结才生效；
- 仲裁不裁决技术问题：它只裁定"验收策略判得对不对"，分账执行仍走结算层。

## v1.6 自持模式 `a2n-node`：没有服务器、没有托管时的那份网络

**没有托管商（积分不上线）时，任意两个人各跑一个节点就能互相发现、互相调用 ——
不需要任何第三方。** 详见 [docs/SOVEREIGN.md](docs/SOVEREIGN.md)。

差距的落点很具体：**发现层本来就没有服务器**（`a2n-p2p` 的 gossip + 多跳按需查询），
断点在"调用"这一环 —— 平台模式下调用要走平台中继、查平台注册表、过平台门禁。
`a2n-node` 就是这三样在去中心形态下的替代品，一个包，平台侧一行没改：

| 环节 | 自持模式怎么做 |
|---|---|
| 身份 | 公钥指纹 `did:a2n:ag_<sha256(pub)[:24]>`；卡自带 `pub` + `sig`，**验卡不需要任何机构** |
| 发现 | 邻居 gossip；本次把「业务入口 + 卡哈希」随 `advert` 一起播出去，取卡可核对 |
| 调用 | 每个节点自开 HTTP 入口，请求直连对方，**自带 DID 签名**（取代明文 `X-Principal`） |
| 守门 | 门禁下沉到节点：先验身份再谈业务，受限能力只吃本地白名单 |
| 凭据 | **双边互签**：供给方签交付收据，调用方签回执引用该收据的签名，两方各存一份 |
| 台账 | 节点自己建表、自己刻章（`a2n-notary`），事实只在**自己的库**里 |
| 防重放 | `nonce` + 时间窗 |
| 钱 | 无托管 ⇒ 无积分增发 ⇒ **没有账本需要一致**（这才是"不需要服务器"的根因） |

```bash
python scripts/sovereign_demo.py    # A ── B ── C：A 只认识 B，却找到了 C 并调用它
python -m pytest tests/test_sovereign.py -q     # 34 项（单元 + 真进程集成）
```

三条纪律：**签名域必须是整份东西**（卡 = 整张卡去 sig，收据 = 整份 body）、
**did 必须由自带公钥推出**（否则"验签通过"是一句讽刺）、
**哈希/指纹/验签口径全项目只有一个实现**（自持模式与平台模式共享同一个卡哈希，
同一把钥匙不会算出两个身份）。

已知边界（不假装有）：跨公网仍需一个"会合点"、没有全局信誉、
双边互证只覆盖两方、撤回/争议/仲裁不在本模式内、密钥即身份。
