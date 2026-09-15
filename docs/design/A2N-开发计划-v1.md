# A2N 开发计划 v1.0

> 配套文档：《A2N 项目全案设计文档 v1.0》
> 本文件回答一个问题：**从零到"第一笔真实资金跑通全链路"，每天写什么、按什么顺序写、什么时候算做完。**

> 更正（2026-09-14）：截至本日期，项目已建成 **23 个包、`pytest 400 passed`**；运行形态为**单进程 FastAPI + SQLite，无 Celery / Redis / PostgreSQL**。下文"技术选型"及若干步骤里的 Celery / Redis / Postgres 描述已就地纠正。结算收口 `daily_cut` 已实现，但默认不自动调度。

---

## 0. 开工前必须钉死的三条工程红线

设计哲学里的三铁律，在工程上必须变成**可执行的约束**，而不是口号。做法：写进代码结构 + 写进测试 + 写进 CI 门禁。

| 铁律 | 工程实现方式 | 强制手段 |
|---|---|---|
| 只发行情，不定价 | 行情板服务只有 `SELECT` 权限的数据库用户，禁止写任何报价表 | DB 角色权限 + 无 `price` 写入接口 |
| 只刻章，不碰钱 | 所有资金动作经 `CustodianAdapter` 抽象层，本地代码永不出现金额变动的"执行"逻辑，只生成**指令单** | 架构隔离 + 静态扫描（core 目录禁止 import 支付 SDK） |
| 廉洁刻进代码 | 积分总量由账本 `SUM` 得出，不存在余额字段；`mint` 只能被"持牌方充值回调"这一个调用点触发 | 单测锁死 + 每日对账任务 |

### 三条不可绕过的技术护栏（第一周就要写）

```python
# tests/test_ledger_invariants.py

def test_no_mint_entrypoint_outside_deposit():
    """全网唯一 mint 调用点必须是 custodian 充值回调"""
    callers = static_find_callers("ledger.mint")
    assert callers == {"core.custodian.webhook.on_deposit_confirmed"}

def test_points_total_equals_escrow():
    """每日对账：积分总量 ≡ 托管余额（分）"""
    assert ledger.total_points_fen() == custodian.query_balance_fen()
    # 不等 → 触发 freeze_withdrawals() + 告警

def test_ledger_is_append_only():
    """账本表无 UPDATE / DELETE 权限"""
    assert db.grants("ledger_entries") == ["INSERT", "SELECT"]
```

**这三条测试挂了，CI 就不许合并。这是整个项目廉洁性的技术底座，不是可选项。**

---

## 1. 技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| 后端主服务 | **Python 3.13 + FastAPI** | 生态成熟、异步、SDK 可复用同一语言 |
| 账本存储 | **SQLite**（单文件） | 需要强事务 + append-only 约束 + 可审计；当前单进程默认 SQLite |
| 心跳/队列/限流 | **进程内状态**（无 Redis） | 节点在线状态、调度队列、提现风控计数，均由单进程 FastAPI 内存维护 |
| 异步任务 | **单进程 FastAPI 内置调度**（无 Celery/Redis） | 验收、分账、对账、链锚定，均在进程内完成 |
| 节点 SDK | **Python 优先**（`a2n-sdk`），二期 Go/Node | 先服务自家 4090 与种子节点 |
| 行情板前端 | **Next.js + ECharts**（或先纯 HTML+ECharts） | 只读展示，先简单后精致 |
| 持牌清算方 | `CustodianAdapter` 抽象 + **MockCustodian** 先跑通 | 合同未签也能开发，签完换驱动 |
| 凭证链锚定 | 本地 hash 链（v0）→ Merkle Root 定期上公链（v1） | 先保证可验证，再保证抗篡改 |
| 部署 | Docker Compose（M0-M2）→ K8s（M3+） | 前期别过度工程 |

**关键决策：先写 `MockCustodian`。** 持牌方的商务流程往往 4-8 周，不能让它卡住整条开发链路。Mock 与真实驱动实现同一接口，签完约只换一个实现类。

---

## 2. 仓库结构

```
a2n/
├── core/                    # L4 账本与积分（项目心脏）
│   ├── ledger.py            #   复式/append-only 记账
│   ├── points.py            #   mint / burn / transfer
│   ├── reconciliation.py    #   每日对账 + 熔断
│   └── models.py
├── custodian/               # 持牌方适配层（唯一碰钱相关代码）
│   ├── adapter.py           #   抽象接口
│   ├── mock.py              #   本地模拟
│   ├── drivers/             #   pingpp/ 连连/ 汇付 ...
│   └── webhook.py           #   充值/打款回调（唯一 mint 入口）
├── dispatch/                # L2 调度引擎
│   ├── matcher.py           #   能力匹配 + 信誉排序
│   ├── reputation.py        #   归一化信誉分
│   └── blacklist.py
├── acceptance/              # L3 验收层
│   ├── auto_qc.py           #   基准集抽检、结构校验、时延
│   ├── manual.py            #   72h 人工确认
│   └── arbitration.py       #   打回仲裁
├── notary/                  # L5 凭证链
│   ├── chain.py             #   hash 指针串联
│   ├── merkle.py
│   └── anchor.py            #   定期上链
├── market/                  # 行情板（只读 API）
│   └── public_api.py
├── settlement/              # 分账指令生成
│   ├── splitter.py          #   90/2/3/5
│   └── withdrawal.py        #   提现冻结、风控、指令下发
├── sdk/                     # 节点 SDK（独立包，可 pip install）
│   └── a2n_sdk/
├── web/                     # 前端
├── ops/                     # 迁移、cron、监控、备份
└── tests/
```

**目录纪律（用 CI 静态检查强制执行）：**
- `core/`、`dispatch/`、`notary/` **禁止** import 任何支付 SDK 或直接发起外部资金请求
- `market/` 只允许 SELECT
- 只有 `custodian/` 能与外部资金系统通信

---

## 3. 数据模型（核心表）

| 表 | 关键字段 | 说明 |
|---|---|---|
| `accounts` | id, kyc_id(持牌方), type(user/node/author/pool/fee), status | 主体 |
| `ledger_entries` **append-only** | id, account_id, delta_fen, ref_type, ref_id, prev_hash, hash, created_at | **积分总量 = SUM(delta)**，不存余额字段 |
| `nodes` | id, account_id, capability(JSONB), capacity, reputation, status, last_heartbeat | 节点 |
| `tasks` | id, requester_id, capability, payload_hash, budget_fen, state, assigned_node_id, result_hash, score | 状态机驱动 |
| `settlement_orders` | id, task_id, splits(JSONB:90/2/3/5), custodian_ref, state | 分账指令单 |
| `withdrawals` | id, account_id, points_fen, state, risk_flags, custodian_ref | 提现 |
| `receipts` | id, event_type, payload, prev_hash, hash | 凭证链 |
| `anchors` | id, merkle_root, chain, txid, block_height | 链上锚定记录 |
| `reputation_events` | node_id, event, weight, task_id | 信誉流水 |

**任务状态机（必须显式建模，别用 if-else 糊）：**
```
CREATED → MATCHED → ASSIGNED → RUNNING → SUBMITTED
   → AUTO_QC → (PASS | FAIL)
   → MANUAL_PENDING →(72h 超时自动通过)→ ACCEPTED
   → ARBITRATION → (ACCEPTED | REJECTED)
   → SETTLING → SETTLED
任意状态 → CANCELLED / TIMEOUT
```

---

## 4. 里程碑与冲刺计划

**节奏：两周一个 Sprint，每个 Sprint 有明确 Demo 与 DoD。**
**团队假设：2 名全栈 + 1 名兼职运维。1 人全职则时间 ×2。**

### Sprint 0（W1-2）：地基 —— 账本先立起来

目标：**一个"不可能增发"的账本。**

- [ ] 仓库初始化、单进程 FastAPI + SQLite（无 Docker / 无 Redis 依赖）、CI（lint + test）
- [ ] 数据库迁移框架（Alembic），建 `accounts` / `ledger_entries`
- [ ] `ledger.py`：append-only 记账，hash 链串联（每条记录含 `prev_hash`）
- [ ] `CustodianAdapter` 抽象 + `MockCustodian`
- [ ] 充值回调 → 唯一 mint 入口，跑通 充100元 → 托管+100 → 积分+100
- [ ] 提现指令 → 冻结 → mock 打款回执 → burn
- [ ] **三条红线测试全部通过并接入 CI**

✅ **DoD**：`test_points_total_equals_escrow` 绿；账本表被撤销 UPDATE/DELETE 权限；Mock 环境下充值-提现闭环跑通。

---

### Sprint 1（W3-4）：最小执行闭环（= 全案 M0）

目标：**你自己的 4090 接到第一个任务并拿到分账。**

- [ ] `a2n-sdk` v0.1：注册 / 能力声明 / 心跳（30s）/ 任务拉取 / 结果上报
- [ ] `dispatch/matcher.py`：能力匹配 + 在线过滤 + 信誉排序 + 前 3 顺位
- [ ] 任务状态机（CREATED → ... → SUBMITTED）
- [ ] 节点摘牌逻辑：心跳超时自动摘牌（节点自治守门）
- [ ] 最小 Agent：`translation-v2` 或 `ocr-pro` 跑一个真实任务
- [ ] 分账指令生成（90/2/3/5 落到 `settlement_orders`）

✅ **DoD**：一条命令 `python -m a2n_sdk run` 后，本机节点被调度、执行、上报、生成分账单；节点积分余额正确增加。**这是全案的 M0 验收标准。**

---

### Sprint 2（W5-6）：验收与凭证链

目标：**让每一笔账都能被第三方独立验证。**

- [ ] `auto_qc.py`：基准测试集抽检、输出结构校验、时延达标判定
- [ ] 人工验收：大单 72h 确认，超时自动通过（单进程内置定时，无 Celery）
- [ ] 打回仲裁流程 + 三联单（任务单 / 结果 hash / 评分）
- [ ] `notary/chain.py`：关键事件（充值、任务、验收、分账、提现）生成 receipt，hash 串联
- [ ] `GET /v1/notary/receipt/{id}` + `POST /v1/notary/verify` 独立验签接口
- [ ] 凭证链自校验脚本（断链即告警）

✅ **DoD**：任取一笔任务，能用一条命令从头到尾验证凭证链完整；人工验收超时自动生效。

---

### Sprint 3（W7-8）：行情板 + 对账熔断

目标：**把"公开可审计"变成产品。**

- [ ] 行情板 API（只读 DB 角色）：
  - `GET /v1/market/agents` 在线类型、供给量、近期成交**均价**（统计事实）
  - `GET /v1/market/compute` 算力供给密度
  - `GET /v1/market/reputation/{id}` 信誉分构成
  - `GET /v1/market/stats` 全网任务量 / 成功率 / 平均时延
- [ ] 前端页面：行情板 + 公开审计页（积分总量 vs 托管余额 + 凭证浏览器）
- [x] `a2n-settlement/closing.py` 的 `daily_cut`：当日对账 + 三数 + 按天覆盖落库 + 报警**已实现**；但默认 `A2N_CLOSING_INTERVAL_SEC=0` **不自动跑**，对账/冻结提现的生产化定时调度仍待办。

> 更正（2026-09-14）：原"每日凌晨自动对账、差一分冻结提现"为计划态；现已落地为 `daily_cut`，但默认 `A2N_CLOSING_INTERVAL_SEC=0` 不自动跑，需运维侧显式调度，不要理解为已自动每日执行。
- [ ] 场外交易声明页（不运营、不推荐、零分润、官方从不收购）+ 公开余额验证工具

✅ **DoD**：审计页对外可访问；人为制造一笔对账差异，系统在 1 分钟内冻结提现并告警。

---

### Sprint 4（W9-10）：接真钱（= 全案 M1）

目标：**真实人民币走完一圈。**

- [ ] 持牌方商户号开通、KYC 接口对接
- [ ] 真实 driver 实现 `CustodianAdapter`（充值 / 分账 / 打款 / 余额查询）
- [ ] 提现风控：限额、T+1、异常模式拦截（进程内计数，无 Redis）
- [ ] 冷启动激励池：运营方**真金白银充值**入池（代码上禁止池子凭空产生积分）
- [ ] 小额全链路验证：充 10 元 → 调用 → 验收 → 提现到卡

✅ **DoD**：**第一笔"充值 → 调用 → 验收 → 提现到银行卡"全链路跑通**，凭证链完整，对账日清。**这是全案 M1 验收标准。**

---

### Sprint 5（W11-12）：信誉体系与种子节点

- [ ] `reputation.py` 归一化信誉分 = f(验收通过率, 任务量, 在线率, 仲裁败诉数)，按能力类别分列
- [ ] 新节点"新签"标记 + 小额试单保护期
- [ ] 降级路径：连续失败 → 降权 → 摘牌；伪造结果 → 永久黑名单 + 扣除未结算额
- [ ] SDK 打包发布（PyPI 私有源 / GitHub）+ 一键部署脚本
- [ ] 种子节点招募：3-5 个外部节点接入

✅ **DoD**：至少 3 个非自有节点稳定在线 7 天；黑名单机制经一次真实事件验证。

---

### Sprint 6（W13-16）：灰度公测与加固

- [ ] 第三方使用方接入、真实任务负载
- [ ] 仲裁流程实战、纠纷 SOP
- [ ] 安全加固：接口鉴权、限流、审计日志、备份与恢复演练
- [ ] 压测：1000 节点心跳 + 100 QPS 任务调度
- [ ] Merkle Root 定期上链（`anchor.py`）
- [ ] 第二家持牌方接入预案（数据可迁移设计验证）

✅ **DoD**：连续 14 天无对账差异；压测指标达标；灾备演练通过。

---

## 5. 第一个可运行闭环：本周就能动手的 12 件事

如果只想先看到东西跑起来，按这个顺序（对应 Sprint 0 + 1 的精简版）：

1. 建仓库 `a2n/`，单进程 FastAPI + SQLite 起库（无 Docker / 无 Redis）
2. 写 `ledger_entries` 表 + append-only 约束（触发器和权限）
3. 写 `ledger.append()`：插入一条记录，带 `prev_hash` / `hash`
4. 写 `points.mint()` / `burn()` / `total()`
5. 写 `CustodianAdapter` 接口 + `MockCustodian`
6. 写充值回调：mock 通知 → mint → 生成 receipt
7. 写 `a2n_sdk`：register / heartbeat / poll_task / submit_result
8. 写 `/v1/nodes/heartbeat` + 进程内在线表（TTL 90s）
9. 写 `dispatch.matcher()`：能力过滤 → 信誉排序 → 取前 3
10. 写任务状态机 + `/v1/tasks` 创建与结果提交
11. 写分账：`settlement.split(90,2,3,5)` 生成指令单
12. 写那**三条红线测试**，跑绿

跑通后，你会在终端看到：节点上线 → 收到任务 → 执行 → 上报 → 账本多了一笔 → 凭证链多了一枚章。

---

## 6. 关键接口清单（v1）

```
# 节点侧
POST /v1/nodes/register          注册（KYC 由持牌方完成）
POST /v1/nodes/capabilities      能力声明
POST /v1/nodes/heartbeat         30s 心跳
GET  /v1/nodes/me/tasks          拉取任务
POST /v1/tasks/{id}/result       上报结果与 result_hash

# 使用方
POST /v1/tasks                   发任务 + 预算冻结
POST /v1/tasks/{id}/accept|reject
POST /v1/tasks/{id}/arbitrate    申请仲裁

# 行情板（只读）
GET  /v1/market/agents|compute|stats
GET  /v1/market/reputation/{id}

# 资金（全部经持牌方）
POST /v1/custodian/webhook       充值/打款回调（唯一 mint 入口）
POST /v1/wallet/withdraw         提现申请
GET  /v1/wallet/balance

# 公证
GET  /v1/notary/receipt/{id}
POST /v1/notary/verify           第三方独立校验
GET  /v1/notary/audit            公开审计页数据
```

---

## 7. 风险与工程对策

| 风险 | 工程对策 | 触发时机 |
|---|---|---|
| 持牌方接入延期卡住全链路 | `MockCustodian` 先行，接口对齐后只换驱动 | Sprint 0 就要做 |
| 被误定性为交易所 | 代码层无撮合引擎、无报价表、无价差分润；行情板纯统计 | 架构即证据，Sprint 3 对外可见 |
| 刷单骗冷启动补贴 | 池子只补贴真实第三方任务；进程内行为模式检测 | Sprint 5 |
| 验收纠纷爆炸 | 前期仅自动验收 + 小额单；仲裁限量人工 | M2 前 |
| 单点依赖持牌方 | 合同约定数据可迁移；`CustodianAdapter` 预留第二驱动 | Sprint 6 |
| 账本被内部篡改 | append-only + hash 链 + 每日对账 + 定期上链 | Sprint 0 起 |

---

## 8. 人力与时间估算

| 配置 | 到 M0（自己的机器接到任务） | 到 M1（真实资金闭环） | 到公测 |
|---|---|---|---|
| 2 全栈 + 1 兼职运维 | **4 周** | **10 周** | 16 周 |
| 1 人全职 | 6-8 周 | 16-20 周 | 24 周+ |
| 1 人兼职（晚上） | 12 周 | 不现实，M1 前建议补人 | — |

**建议：M1 之前至少要 2 个人。** 一个人同时写账本、SDK、调度和支付对接，廉洁性相关的交叉检查就失去了意义——而那恰恰是这个项目唯一不能出错的地方。

---

## 9. 每个 Sprint 的 Definition of Done（通用）

- [ ] 功能 Demo 可现场演示
- [ ] 核心路径有单测，**红线测试全绿**
- [ ] 账本对账无差异
- [ ] 新增关键事件已接入凭证链
- [ ] 公开文档/API 已更新
- [ ] 无新增"碰钱"代码越过 `CustodianAdapter`
- [ ] 部署脚本可一键回滚

---

## 10. 分布式边界：什么分布，什么不分布

**结论先行：A2N 是"分布式执行 + 中心化可验证清算"，不是 P2P 账本。**

| 维度 | 是否分布式 | 说明 |
|---|---|---|
| 任务执行（L1） | ✅ 完全分布式 | 任何机器装 SDK 即可成为节点，可随时离线 |
| Agent 运行时 | ✅ 分布式 | 节点自持算力与模型 |
| 行情数据消费 | ✅ 公开只读 | 任何人可拉取、可镜像 |
| 凭证链验证 | ✅ 人人可独立验 | 下载全量即可重放，得出同一结论 |
| **账本写入** | ❌ 单一权威 | 只有运营方写入，节点只有只读副本 |
| **资金动作** | ❌ 持牌方独占 | 托管余额的真相在银行/支付机构，不在任何链上 |

### 为什么账本不能做成 P2P

1. **托管余额是链外事实，不参与投票。** 一万个节点投票说"账上有 100 万"，银行实际余额是 99 万，那少的 1 万谁补？P2P 共识只能保证链内一致，无法锚定链外真相。而 A2N 的全部公信力恰恰建立在"积分总量 ≡ 托管余额"这个外部等式上。
2. **P2P 会重新打开"增发"这个后门。** 一旦每个节点都能写账本，女巫节点自记一笔在技术上就成立了，铁律三（代码层无增发）当场失效。宁可要一个可被万人监督的中心，不要一万个无人负责的中心。
3. **调度需要强一致的全局视图。** 谁在线、信誉多少、某任务是否已分账——gossip 的最终一致性会导致重复调度、重复分账，直接造成资金错付。钱的事不能"最终一致"。
4. **合规要求可中止、可追责的主体。** KYC、反洗钱、冻结账户、纠纷仲裁都需要一个能被监管机构找到门牌号的实体。P2P 网络没有这个实体，持牌方的分账指令也没有接收方。

### 那"每个人下载一个账本"这件事还要不要做

**要，但下载的是只读副本，不是写入权。** 具体三件事，按性价比排序：

- **Merkle 轻客户端证明（推荐，Sprint 2 顺手做）**：节点不必下载全量账本，只需拿到与自己相关的分支 + root，即可证明"我这一笔被正确记账了"。全量账本 100GB 也只影响运营方，不影响节点。
- **只读镜像 + 公开导出（Sprint 3）**：账本与凭证链全量导出，任何第三方可镜像、可重放、可对账。运营方篡改数据，镜像站当场打脸。
- **见证节点（witness node，Sprint 6 可选）**：邀请种子节点、合作方、甚至监管机构跑只读见证节点，实时同步并独立校验。运营方账本一旦与见证节点分叉，公信力立刻破产。

**这是"用分布式监督中心化"，不是用分布式替代中心化。** 对 A2N 这个定位——一个靠廉洁性吃饭的公证处——前者是资产，后者是自杀。

### 什么时候该重新考虑 P2P

只有当项目放弃"人民币持牌托管"这一前提、改为纯加密资产结算时，P2P 账本才有意义。但那是另一个项目，且合规风险高出两个数量级。在那之前，**别用技术手段解决一个制度问题。**

---

## 11. 一句话

全案的问题是"做什么"，这份计划的问题是"明天早上打开编辑器先写哪一行"。

答案是：**先写账本，再写那三条测试，然后才轮到 SDK。**
因为一个不可能增发的账本，是这个项目所有公信力的起点；SDK 晚两周写，只是晚两周接到任务；账本写错了，整个项目就没有存在的理由。

去打区块。
