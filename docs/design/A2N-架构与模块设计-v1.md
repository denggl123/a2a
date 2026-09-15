# A2N 架构与模块设计 v1.0

> 目标：一份能支撑 18 个月演进的模块切分，保证 **功能解耦**（改一处不炸全局）与 **可替换**（持牌方、验收策略、链、数据库都能换）。
> 形态选择：**模块化单体（Modular Monolith）**，不是微服务。理由见 §1。

---

## 1. 架构总纲：一个进程，四条纪律

### 为什么不是微服务

钱的系统最怕分布式事务。A2N 的核心不变式是"积分总量 ≡ 托管余额"，一旦账本写入和凭证记录跨服务，就得引入 Saga 或 2PC，而 **2 人团队 + 强一致账务 + 每天对账** 这个组合下，微服务带来的一致性风险远大于它的收益。

**但要按"明天就可能拆"的约束来写单体**——模块之间只用两种通信方式：领域事件（异步）与 Port 接口（同进程调用）。将来真要拆，只是把传输层从函数调用换成 RPC，业务代码不动。

### 四条纪律（用静态检查强制，不靠自觉）

| 纪律 | 内容 | 强制手段 |
|---|---|---|
| **D1 数据所有权** | 每张表只属于一个模块，其他模块只能通过 API 或事件访问，禁止跨模块 JOIN | 分 schema + DB 权限 + 测试 |
| **D2 依赖单向** | `interface → application → domain → kernel`，domain 永不 import 外部 SDK | import-linter 契约测试 |
| **D3 通信默认异步** | 跨模块默认发领域事件；同步调用只允许"读"，且必须走 Port | 架构测试 |
| **D4 外部依赖走 Port** | 数据库、支付、KYC、链、时钟、ID 生成器全部接口化 | adapter 目录隔离 |

---

## 2. 模块全景

### 2.1 分层与模块清单

```
┌─ 接入层 interface ────────────────────────────────────────┐
│  M12 API 网关        M13 节点 SDK        M14 行情板前端     │
└──────────────────────────────────────────────────────────┘
┌─ 领域层 domain ───────────────────────────────────────────┐
│  M1 身份   M2 账本   M3 任务   M4 调度                     │
│  M5 验收   M6 分账   M7 钱包   M8 信誉                     │
└──────────────────────────────────────────────────────────┘
        ↓ 领域事件（Outbox 保证与账本同事务）
┌─ 支撑层 support ──────────────────────────────────────────┐
│  M9 行情投影（只读）        M10 凭证链公证                  │
└──────────────────────────────────────────────────────────┘
┌─ 适配器层 adapters（Port 的实现，可整体替换）──────────────┐
│  A1 持牌方   A2 KYC/KYA   A3 链锚定   A4 存储   A5 通知    │
└──────────────────────────────────────────────────────────┘
┌─ 横切 kernel ─────────────────────────────────────────────┐
│  X1 事件总线+Outbox   X2 策略注册表   X3 可观测与审计       │
└──────────────────────────────────────────────────────────┘
```

### 2.2 领域模块职责表

| 模块 | 职责 | **明确不做** | 独占数据 | 主要扩展点 |
|---|---|---|---|---|
| **M1 identity** | 主体（用户/节点/作者/池/平台）、KYC 绑定、**KYA 智能体注册与风险分级**、能力声明（A2A Agent Card） | 不判断信誉、不参与调度 | accounts, agents, capabilities | CapabilitySchema |
| **M2 ledger** | 积分账本：append-only 记账、hash 链、总量计算、冻结/解冻 | **不知道任务是什么**，不碰钱 | ledger_entries, freezes | 无（刻意封闭） |
| **M3 task** | 任务状态机、预算冻结、结果接收、超时与取消 | 不选择节点、不判定质量 | tasks, task_events | TaskType 扩展 |
| **M4 dispatch** | 匹配、排序、路由、试单保护、摘牌 | 不执行、不验收 | **本地节点投影**（非权威） | **RoutingPolicy** |
| **M5 acceptance** | 质检、人工确认、打回仲裁，产出裁决 | 不付款、不改账 | verdicts, arbitrations | **AcceptancePolicy** |
| **M6 settlement** | 分账规则、生成指令单、下发持牌方、处理回执、**结算收口（`record`/`daily_cut`）** | 不持有资金、不碰账户 | settlements（task_id 主键=幂等键）、reconciliations（按天 UNIQUE） | **SplitRule** |
| **M7 wallet** | 充值入账、提现申请、冻结、风控、限额 | 不打款（交给持牌方） | withdrawals | **RiskRule** |
| **M8 reputation** | 信誉事件聚合、评分、降级建议 | 不直接调度、不封禁（只出建议） | reputation_scores, events | **ReputationModel** |

### 2.3 支撑与适配

| 模块 | 职责 | 关键约束 |
|---|---|---|
| **M9 market 行情投影** | 行情板只读读模型（供给量、成交均价统计、算力密度、信誉榜） | **只有 SELECT 权限；不含任何定价逻辑** |
| **M10 notary 凭证链** | 订阅全部关键事件 → hash 串联 → Merkle → 定期锚定 → 公开验证接口 | 只消费，不影响业务 |
| **A1 custodian** | 实现 `CustodianPort`：充值回调、分账指令、打款、余额查询 | 唯一能与外部资金系统通信的目录 |
| **A2 kyc/kya** | 实名、智能体身份核验、风险分级 | 可换供应商 |
| **A3 anchor** | 链上锚定策略（不锚定 / 每小时 / Merkle 每千条） | 可关闭 |
| **X1 event bus** | 领域事件发布 + Outbox（与业务同事务） | 至少一次投递，消费端幂等 |
| **X2 policy registry** | 策略插件注册、按 key 解析、**规则版本化** | 见 §6 |

---

## 3. 依赖规则

### 3.1 允许与禁止

| 从 ↓ / 到 → | M1 | M2 | M3 | M4 | M5 | M6 | M7 | M8 | M9 | M10 | A* |
|---|---|---|---|---|---|---|---|---|---|---|---|
| M3 task | 读 | 读 | — | 事件 | 事件 | — | — | — | — | — | ✗ |
| M4 dispatch | 读 | ✗ | 事件 | — | ✗ | ✗ | ✗ | 读 | ✗ | ✗ | ✗ |
| M5 acceptance | ✗ | ✗ | 事件 | ✗ | — | 事件 | ✗ | ✗ | ✗ | ✗ | ✗ |
| M6 settlement | 读 | **写** | 事件 | ✗ | 事件 | — | ✗ | ✗ | ✗ | ✗ | ✓(A1) |
| M7 wallet | 读 | **写** | ✗ | ✗ | ✗ | ✗ | — | ✗ | ✗ | ✗ | ✓(A1) |
| M8 reputation | ✗ | ✗ | ✗ | ✗ | 事件 | ✗ | ✗ | — | ✗ | ✗ | ✗ |
| M9 market | 只读投影 | 只读投影 | 只读投影 | 只读投影 | 只读投影 | ✗ | ✗ | 只读投影 | — | ✗ | ✗ |
| M10 notary | 订阅 | 订阅 | 订阅 | 订阅 | 订阅 | 订阅 | 订阅 | 订阅 | ✗ | — | ✓(A3) |

**读 = 通过 Port 同步读；事件 = 只发事件不等回执；✗ = 禁止。**

### 3.2 关键解耦点（三个，务必照做）

1. **dispatch 不依赖 identity 的运行时。** 调度要的是"在线节点 + 能力 + 信誉"，它维护自己的**本地投影**（由 `NodeRegistered` / `CapabilityUpdated` / `NodeHeartbeat` / `ReputationUpdated` 事件更新）。identity 挂了，调度照常。
2. **ledger 不知道业务。** 它只认 `post(entry)` 和 `ref_type/ref_id`。任务、分账、提现对它而言都是一串引用。这让账本可以独立审计，也让"新增一种积分流转"不需要改账本。
3. **notary 是纯订阅者。** 它挂掉不影响任何业务，只影响"章还没刻"。挂了补刻即可——这正是公证层应有的失败语义。

### 3.3 静态检查契约（CI 强制）

```ini
# .importlinter
[importlinter]
root_package = a2n

[importlinter:contract:domain-purity]
name = 领域层不得依赖外部 SDK
type = forbidden
source_modules = a2n.domain
forbidden_modules =
    a2n.adapters
    stripe, alipay, wechatpy, web3, redis  # 支付/链/缓存客户端

[importlinter:contract:market-readonly]
name = 行情投影不得反向依赖领域服务
type = independence
modules = a2n.support.market

[importlinter:contract:ledger-closed]
name = 账本只被结算与钱包写入
type = forbidden
source_modules = a2n.domain.task, a2n.domain.dispatch, a2n.domain.acceptance
forbidden_modules = a2n.domain.ledger
```

---

## 4. 数据所有权

| Schema | 归属模块 | 表 | 其他模块访问方式 |
|---|---|---|---|
| `ledger` | M2 | ledger_entries(hash, prev_hash), freezes | 仅 `LedgerPort` |
| `identity` | M1 | accounts, agents, agent_cards, kya_grades | 只读 API / 事件 |
| `task` | M3 | tasks, task_events, payloads | 事件 |
| `dispatch` | M4 | node_projection（可重建） | 私有 |
| `acceptance` | M5 | verdicts, arbitrations, qc_baselines | 事件 |
| `settlement` | M6 | settlements（task_id 主键=幂等键）, reconciliations（按天 UNIQUE） | 事件 |
| `wallet` | M7 | withdrawals, risk_flags | 事件 |
| `reputation` | M8 | reputation_scores, reputation_events | 只读 API |
| `notary` | M10 | receipts, anchors, merkle_batches | 公开只读 |
| `market`（独立库） | M9 | 物化视图 | 无人依赖 |

**规则：跨 schema 的 JOIN 一律禁止。** 需要联合数据就建投影。

---

## 5. 领域事件目录

| 事件 | 发布者 | 订阅者 | 关键载荷 |
|---|---|---|---|
| `deposit.confirmed` | A1 custodian | M2（唯一 mint）, M10 | amount_fen, custodian_ref |
| `points.minted / burned / frozen` | M2 | M10, M9 | delta, ref |
| `task.created` | M3 | M4, M9 | capability, budget_fen |
| `task.assigned` | M4 | M3, M9 | node_id, routing_policy_version |
| `task.submitted` | M3 | M5 | result_hash, elapsed_ms |
| `acceptance.passed / failed` | M5 | M6, M8, M9 | score, policy_version |
| `arbitration.requested / resolved` | M5 | M6, M8 | verdict, arbitrator |
| `settlement.ordered` | M6 | M10 | splits, rule_version, custodian_ref |
| `settlement.settled / failed` | M6 | M3（回退/重试）, M8, M10 | so_id |
| `withdrawal.requested / frozen / paid` | M7 | M2, M8, M10 | amount, risk_flags |
| `node.registered / heartbeat / suspended / blacklisted` | M1, M4 | M4(投影), M8, M9 | node_id |
| `reputation.updated` | M8 | M4(投影), M9 | node_id, score |
| `reconciliation.failed` | X3 对账任务 | M7（冻结提现）, 告警 | diff_fen |

**幂等约定**：所有消费端必须按 `event_id` 去重；事件至少一次投递。

---

## 6. 扩展点：策略插件化

所有"将来一定会变"的业务规则，都定义成策略接口 + 注册表，**不写死在流程里**。

| 扩展点 | 接口 | 默认实现 | 何时替换 |
|---|---|---|---|
| `RoutingPolicy` | `rank(candidates, task) -> list[node_id]` | `ReputationFirstPolicy` | 要加地域亲和、成本优先、灰度 |
| `AcceptancePolicy` | `judge(task, result) -> Verdict` | `BaselineSamplePolicy` | 接 LLM-as-judge、人工、TEE 多方验证 |
| `SplitRule` | `split(amount, ctx) -> Splits` | `Fixed90_2_3_5` | 版税表、阶梯分成、品类差异 |
| `ReputationModel` | `score(events) -> float` | `DefaultWeightedModel` | 分类别模型、引入仲裁胜率 |
| `RiskRule` | `check(withdrawal) -> RiskVerdict` | `AmountLimitRule + T1Rule` | 加异常模式检测、设备指纹 |
| `AnchorStrategy` | `should_anchor(batch) -> bool` | `MerkleEvery1000` | 换链、提高频率 |
| `CapabilitySchema` | `parse(card) -> Capability` | `A2AAgentCardV1` | 支持自定义能力描述 |
| `CustodianDriver` | `CustodianPort` 实现 | `MockCustodian` | 换/增持牌方 |

### 规则版本化（必做，否则两年后无法审计）

```python
@dataclass(frozen=True)
class PolicyRef:
    key: str          # "split.fixed"
    version: str      # "2026.09.01"
    params: dict      # {"node": 0.90, "author": 0.02, ...}

# settlement_order 与 verdict 必须持久化 PolicyRef
# 审计时：用当时的规则重放，验证结果与当年一致
```

**没有版本化的规则，等于没有规则。** 监管问"这两年前这笔单为什么这么分"，你回答"现在的规则是这样"是不合格的。

---

## 7. 一次调用的完整链路（事件编排，无集中编排器）

```
使用方 POST /tasks
   └─ M3 task: 创建 + 冻结预算 ──→ task.created
        └─ M4 dispatch: 本地投影匹配 + RoutingPolicy 排序 ──→ task.assigned
             └─ M13 SDK 执行（节点侧，网络外）
                  └─ M3: 收结果 + result_hash ──→ task.submitted
                       └─ M5 acceptance: AcceptancePolicy 判定 ──→ acceptance.passed
                            ├─ M6 settlement: SplitRule 生成指令
                            │     ├─ M2 ledger: 记账（同事务写 Outbox）
                            │     ├─ A1 custodian: 下发分账指令
                            │     └─ settlement.settled ──→ M8 更新信誉
                            ├─ M10 notary: 订阅全部事件 → hash 链 → Merkle
                            └─ M9 market: 更新只读投影
```

**失败语义**：任一步失败只发事件，由订阅方各自补偿（如 `settlement.failed` → M3 回退任务状态）。**没有集中 saga，就不存在"编排器挂了全链路瘫痪"。**

---

## 8. 六条解耦手法（本项目专属）

1. **Transactional Outbox**：账本写入与事件发布同一事务，杜绝"记了账没发事件"。
2. **CQRS 只读投影**：行情板与调度视图各自维护，主库压力与读模型解耦，也天然实现"行情板只有 SELECT"。
3. **事件编排（choreography）而非集中编排**：模块之间只认事件，不认彼此。
4. **Port & Adapter**：持牌方、KYC、链、存储全可换，且换的时候领域代码零改动。
5. **策略注册表 + 规则版本化**：业务规则可热切换、可追溯、可回放。
6. **架构即合规证据**：`domain` 目录里搜不到任何支付 SDK，`market` 里搜不到任何写操作，`ledger.mint` 只有一个调用者——静态检查报告本身就是给监管看的材料。

---

## 9. 目录结构

```
a2n/
├── kernel/                  # X1-X3 横切
│   ├── events.py            #   事件总线 + Outbox
│   ├── policy.py            #   策略注册表 + PolicyRef
│   ├── ports.py             #   Clock / IdGen / Hash / Tx
│   └── observability.py
├── domain/
│   ├── identity/            # M1
│   ├── ledger/              # M2  ← 最封闭，最该被审计
│   ├── task/                # M3
│   ├── dispatch/            # M4
│   ├── acceptance/          # M5
│   ├── settlement/          # M6
│   ├── wallet/              # M7
│   └── reputation/          # M8
├── support/
│   ├── market/              # M9  只读投影
│   └── notary/              # M10 凭证链
├── adapters/
│   ├── custodian/           # A1  mock / 真实驱动
│   ├── kyc/                 # A2
│   ├── anchor/              # A3
│   ├── persistence/         # A4  Postgres 仓储实现
│   └── notify/              # A5
├── interface/
│   ├── http/                # M12 API 网关
│   ├── sdk/                 # M13 节点 SDK（独立发包）
│   └── web/                 # M14 行情板前端
└── tests/
    ├── architecture/        # import-linter + 依赖契约测试
    └── invariants/          # 三条红线测试
```

---

## 10. 架构决策记录（ADR 摘要）

| # | 决策 | 备选 | 理由 |
|---|---|---|---|
| ADR-1 | 模块化单体 | 微服务 | 账务强一致优先；2 人团队；按可拆约束写，未来能拆 |
| ADR-2 | 事件编排 | 集中 Saga | 无单点；失败语义局部化 |
| ADR-3 | 账本不感知业务 | 账本带业务字段 | 可独立审计；新增流转不改账本 |
| ADR-4 | 调度用本地投影 | 实时查 identity | 依赖解耦；identity 故障不影响调度 |
| ADR-5 | 规则版本化持久化 | 只存当前规则 | 监管可追溯；历史可回放 |
| ADR-6 | 先实现 MockCustodian | 等签约再开发 | 商务流程 4-8 周，不能卡住开发 |

---

## 11. 演进路线

| 阶段 | 形态 | 动作 |
|---|---|---|
| 现在 | 单进程 FastAPI + SQLite（无 Celery / Postgres / Redis） | 按上述边界写 |
| 需要时（最先拆） | **M9 market 独立** | 无状态只读，拆了最省事，还能扛公开流量 |
| 其次 | **M10 notary 独立** | 纯异步消费，天然可独立部署 |
| 再次 | **M4 dispatch 独立** | 有状态但可水平扩展，投影可重建 |
| **永不拆** | **M2 ledger + M6 settlement + M7 wallet** | 必须与账本同事务，拆了就是给自己找麻烦 |

---

## 12. 一句话

这套架构的所有设计，都指向同一个目标：**让"廉洁"这个抽象承诺，变成可以被静态检查、被单测、被审计的具体结构。**

目录边界就是合规边界，import 规则就是廉洁规则，策略版本就是给两年后监管的答案。
