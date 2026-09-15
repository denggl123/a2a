# A2N 注册与发现模块设计（基于 A2A）v1.0

> 范围：M1 identity 模块的核心部分 —— **多维度注册、生命周期管理、能力发现**
> 依赖基础：**A2A v1.0**（Agent Card + 签名 + 任务生命周期）
> 记账单位：**积分（point）**，1 积分 = 0.01 元，整数记账
> 本文是《A2N-架构与模块设计-v1.md》中 M1 模块的展开。

---

## 1. 定位：注册表是索引，不是权威

### 三条边界

| 角色 | 谁持有 | 说明 |
|---|---|---|
| **能力声明的权威** | **节点自己** | Agent Card 由节点自持并签名，A2N 不代写、不代改 |
| **索引与背书** | **A2N 注册表** | 拉取 card、验证签名、存 hash、建索引、发背书签名 |
| **评判** | **信誉模块 M8** | 注册表只存信誉快照与引用，不计算 |

**一句话：节点自证能力（卡由节点自持自签，A2N 用 `verify_card`/`card_verdict` 验证并返回三态：已自证 `signed` / 未自证 `unattested` / 验不过 `invalid`·被改 `tampered`）；未验过或验不过的卡**不再可发现、更不可直接调用**，A2N 也不冒充"验过"；信誉模块负责"他干得怎么样"。**

> 更正（2026-09-14）：卡片自证自 9/12 起收口为三态诚实闸。发现路与派单路（assignable）共用同一判据 `card_verdict`：自称身份却验不过（`invalid`）或卡被改过（`tampered`）一律排除，未声明身份（`unattested`）只可发现、不算已自证、也不得冒充验过。注册关口 `_guard_selfproof`：自称有身份却验不过 → 拒收；自签卡必须自带 `uid`（平台不补）。不再"按节点自报信任"一刀切放行。

### 注册表明确不做

- ❌ 不定价（`price_hint` 只是节点自填的提示，注册表不校验、不比较、不排序时作为主要因子）
- ❌ 不撮合（只返回候选列表，不决定成交）
- ❌ 不保管资金（只存 `settlement.account_ref` 引用）
- ❌ 不替节点做 SLA 承诺

---

## 2. 基于 A2A 的注册：Card 扩展规范

### 2.1 原则：复用 A2A 原生字段，只加必要的扩展

能用 A2A 原生字段表达的，一律用原生（`skills`、`capabilities`、`authentication`、`provider`）。
只有 A2A 不管、而 A2N 必须知道的，才放进 `x-a2n` 扩展命名空间。

### 2.2 Agent Card 扩展示例

```json
{
  "name": "ocr-pro-node-07",
  "version": "2.1.0",
  "url": "https://node07.example.com/a2a",
  "capabilities": {
    "streaming": true,
    "extensions": [{ "uri": "https://a2n.dev/ext/v1", "required": true }]
  },
  "skills": [
    {
      "id": "ocr-pro",
      "name": "高精度 OCR",
      "description": "印刷体/手写体识别，支持中英日",
      "tags": ["ocr", "document", "zh", "en"],
      "inputModes": ["image/png", "application/pdf"],
      "outputModes": ["application/json"]
    }
  ],
  "x-a2n": {
    "node_id": "n_8f3a",
    "registry": "https://reg.a2n.dev",
    "compute": {
      "gpu": "4090", "vram_gb": 24, "cpu_cores": 16,
      "region": "cn-east-2", "concurrency": 4
    },
    "sla": {
      "max_latency_ms": 2000,
      "availability_target": 0.95,
      "max_concurrent": 4
    },
    "price_hint": {
      "ocr-pro": { "amount": 1, "unit": "point_per_call" }
    },
    "settlement": { "account_ref": "acct_x9", "custodian": "mock" },
    "compliance": { "kya_grade": "B", "kyc_status": "verified", "jurisdiction": "CN" },
    "reputation_ref": "https://reg.a2n.dev/reputation/n_8f3a",
    "heartbeat": { "interval_s": 30 }
  },
  "signature": { "..." : "A2A v1.0 签名域" }
}
```

### 2.3 扩展设计要点

| 字段 | 为什么必须有 | 备注 |
|---|---|---|
| `compute` | A2A 只描述"能做什么"，不描述"能扛多少" | 调度做容量控制的依据 |
| `sla` | 验收模块需要判定标准 | 无 SLA 则无法自动验收 |
| `price_hint` | 使用方需要预算估计 | **提示价，非报价**，注册表不据此定价 |
| `settlement` | 分账要知道打给谁 | 只存 `account_ref` 引用，不含账户实体 |
| `compliance` | 公约要求 KYA 分级与全链路传递 | 见 §6 |
| `reputation_ref` | 信誉在别的模块，注册表只存指针 | 避免双写不一致 |
| `heartbeat` | A2A 本身没有心跳机制 | 需要扩展 |

---

## 3. 多维度数据模型：六个维度

发现的本质是**在六个维度上求交集 + 在一个维度上排序**。

| # | 维度 | 数据 | 来源 | 用途 |
|---|---|---|---|---|
| D1 | **能力** | skill id + version + tags + 输入输出模态 | Card（节点自证） | 硬匹配 |
| D2 | **资源** | GPU/VRAM/CPU/地域/并发 | Card（节点自证） | 硬过滤 |
| D3 | **质量** | SLA：时延上限、可用率目标、并发上限 | Card（节点自证） | 硬过滤 + 验收基准 |
| D4 | **合规** | KYA 等级、KYC 状态、司法辖区 | 注册表背书（KYA 适配器核验） | 硬过滤 |
| D5 | **信誉** | 归一化分数、分类别分数、仲裁败诉数 | M8 计算，注册表存快照 | **排序** |
| D6 | **经济** | price_hint、结算账户引用 | Card + 注册表 | 预算校验（不排序） |

**关键区分：D1-D4 是节点自己说的（自证），D4 的 KYA 与 D5 的信誉是网络认证的（背书）。**
发现时二者都可查，但信誉排序只能用 D5——因为只有它是第三方验证过的事实。

---

## 4. 生命周期管理

### 4.1 状态机

```
                    ┌──────────┐
   提交 card ──────→│ PENDING  │ 已提交，未验证
                    └────┬─────┘
              验证签名/域名/KYC ↓
                    ┌──────────┐
                    │ VERIFYING│
                    └────┬─────┘
              KYA 分级完成 ↓
                    ┌──────────┐  试单期：小额、限流、白名单任务
                    │ PROBATION│  ──连续 N 单达标──→ ACTIVE
                    └────┬─────┘  ──失败率超阈值──→ DELISTED
                         ↓
                    ┌──────────┐
       ┌───────────→│  ACTIVE  │←──────────┐
       │            └────┬─────┘           │
       │      连续失败 ↓   │ 心跳恢复       │
       │            ┌──────────┐           │
       │            │ DEGRADED │           │
       │            └────┬─────┘           │
       │      心跳丢失 ↓                    │
       │            ┌──────────┐           │
       └────────────│SUSPENDED │───────────┘
                    └────┬─────┘
             超时未恢复 / 恶意行为 ↓
                  DELISTED  /  BLACKLISTED
```

### 4.2 关键规则

| 规则 | 内容 | 理由 |
|---|---|---|
| **试单保护期** | 新节点进入 PROBATION，只接小额单（如 ≤ 10 积分）、限并发 1 | 防刷单、防冷启动期被劣质节点污染 |
| **心跳摘牌** | 30s 心跳，连续 3 次丢失 → SUSPENDED；24h 未恢复 → DELISTED | 节点自治守门，不靠人工 |
| **Card 变更即重验** | `card_hash` 变化触发重新验证；能力降级不影响已调度任务 | 防止"先注册强的、再改成弱的" |
| **黑名单不可逆** | 伪造结果 → BLACKLISTED，未结算额扣除，永久不可再注册 | 廉洁性 |
| **状态变更全程发事件** | 见 §7 | 下游投影更新 |

---

## 5. 发现：三级漏斗

```
全网注册节点  ──①能力匹配（硬）──→ 候选集
             ──②属性过滤（硬）──→ 合格集
             ──③信誉排序（软）──→ 前 3 顺位
```

### ① 能力匹配（D1）

```python
def match(skill_id, version_range, input_modes):
    # 结构化：skill_id + 语义化版本区间 + 输入输出模态交集
    # 可选：tags 的向量召回，用于"没有完全同名 skill"时的近似能力发现
    return candidates
```

版本兼容规则遵循 A2A 的语义化版本约定：`>=2.0 <3.0` 视为兼容；主版本不同视为不同能力。

### ② 属性过滤（D2/D3/D4）

```
region ∈ 允许地域
vram_gb >= 任务要求
max_latency_ms <= 任务 SLA 要求
kya_grade ∈ {A, B}          # 大额任务要求 A
status == ACTIVE 且 online == true
node_id ∉ blacklist
```

### ③ 排序（D5）

```python
score = w1 * reputation_normalized      # 主排序键
      + w2 * sla_attainment             # SLA 达标率
      - w3 * latency_p50_penalty
      + w4 * probation_bonus            # 新节点加权，避免马太效应
```

**注意：`price_hint` 不参与排序。** 一旦按提示价排序，注册表就事实上成了"报价排序器"，滑向定价。这是需要写进代码注释的红线。

### 查询接口

```json
POST /v1/discovery/query
{
  "require": { "skill": "ocr-pro", "skill_version": ">=2.0 <3.0",
               "input_modes": ["image/png"] },
  "filter":  { "region": ["cn-east"], "vram_gb": {">=": 16},
               "kya_grade": ["A","B"], "online": true },
  "sort":    [ { "reputation": "desc" }, { "latency_p50": "asc" } ],
  "limit":   20
}
```

返回：候选列表 + **每个候选的 card_hash 与注册表背书签名**（客户端可离线验证，不必再查注册表、也不必信任返回内容本身）。

### 5.1 可见性等级

不是所有 agent 都想被公开搜到。

| 等级 | 可被搜索 | 可被直接调用 | 典型场景 |
|---|---|---|---|
| `public` | ✅ | ✅ | 公开能力，产能充足 |
| `unlisted` | ❌ 不出现在结果中 | ✅ 知道 agent_id 即可 | 产能有限、定向合作 |
| `private` | ❌ | 仅白名单使用方 | 企业内部 agent、合规限制 |

**这一级是"我的市场列表"能成立的前提**：我可以通过手动添加 ID 的方式使用 `unlisted` / `private` 的 agent，而它们不必暴露给全网。

### 5.2 按需拉取，不下发全量

- **不提供"导出全量目录"接口**（备案导出只给监管，不给客户端）
- 分页 + 游标，单次返回上限（如 100）
- 客户端可缓存自己选中的，禁止缓存全量
- 读压力 ∝ 查询量，与"节点数 × 使用方数"无关，无 fan-out 风暴

### 5.3 指定派单时的轻量校验

使用方可以自由搜、自由选、指定 agent，但**派单那一刻调度器必须做一次校验**——因为涉及钱：

| 校验项 | 不通过时 |
|---|---|
| `status == ACTIVE` | 拒绝，返回状态 |
| 在线（心跳未过期） | 顺位下一位 |
| 不在黑名单 | 拒绝并告警 |
| `card_hash` 与请求方持有的一致 | 提示更新后重试（防能力被偷偷改弱） |
| KYA 等级满足任务金额要求 | 拒绝，提示等级不足 |
| 未超出该节点并发上限 | 顺位下一位 |

**发现是自由的，派单是受控的。** 前者是信息，后者是钱——两者的严格程度天然不同。

> 更正（2026-09-14）：派单前轻量校验（`assignable`）现与发现路共用 `card_verdict` 三态判据——被拒收的卡（`invalid`/`tampered`）既不可发现也不可调用。注意"可直接调用"必须**同时**满足"能付费（服务端门禁 `gate.resolve` 通过）"与"已自证（`selfproof == signed`）"；未自证（`unattested`）的卡只可发现、不可直接调用。

---

## 6. KYA 集成（公约合规）

《智能体支付应用自律公约》要求：智能体身份识别与验证、**高中低风险分级**、身份信息在支付全链路传递。

| 公约要求 | A2N 实现 |
|---|---|
| 智能体身份识别验证 | Agent Card 签名 + 域名验证 + `node_id` 唯一标识 |
| 风险分级 | `compliance.kya_grade`（A/B/C）由 KYA 适配器评定 |
| 全链路传递 | `kya_grade` 写入每个任务的凭证链记录 |
| 用户实名与对应关系 | `accounts` 表绑定持牌方 KYC 主体，agent 与主体一一对应 |
| 备案要求 | 注册表导出备案清单（agent_id / 主体 / 能力 / 分级） |

**KYA 分级建议标准（v1）：**

| 等级 | 条件 | 可接任务 |
|---|---|---|
| **A** | 实名主体 + 域名验证 + 信誉 ≥ 0.9 + 稳定在线 30 天 | 无限制 |
| **B** | 实名主体 + 域名验证 | 单笔 ≤ 1000 积分 |
| **C** | 仅实名主体（PROBATION 期） | 单笔 ≤ 10 积分 |

---

## 7. 领域事件

| 事件 | 时机 | 订阅者 |
|---|---|---|
| `node.registered` | PENDING 创建 | M4（投影）、M10（刻章） |
| `node.verified` | 签名/KYC/KYA 通过 | M4、M9 |
| `node.probation_entered` / `probation_passed` | 试单期起止 | M4 |
| `capability.updated` | card_hash 变化 | M4（重建索引）、M9、M10 |
| `node.heartbeat` | 每 30s（只在状态变化时发事件，常态只写在线表） | M4 |
| `node.degraded` / `suspended` / `delisted` | 状态迁移 | M4、M8、M9、M10 |
| `node.blacklisted` | 恶意行为 | 全部（M3 拒单、M6 扣未结算额） |

---

## 8. 数据模型与索引

```sql
-- 主体（节点/作者/使用方共用）
CREATE TABLE agents (
  agent_id      TEXT PRIMARY KEY,      -- a_xxxx
  principal_id  TEXT NOT NULL,         -- 关联 accounts（持牌方 KYC 主体）
  type          TEXT NOT NULL,         -- node | author | user
  status        TEXT NOT NULL,         -- 见状态机
  kya_grade     TEXT,                  -- A | B | C
  card_url      TEXT NOT NULL,
  card_hash     TEXT NOT NULL,         -- 当前 card 的 sha256
  card_version  TEXT NOT NULL,
  signature     JSONB,                 -- A2A 签名
  domain        TEXT,                  -- 域名验证结果
  registered_at TIMESTAMPTZ,
  last_seen_at  TIMESTAMPTZ
);

-- card 历史版本（不可变，审计用）
CREATE TABLE agent_cards (
  agent_id   TEXT, card_hash TEXT, payload JSONB, fetched_at TIMESTAMPTZ,
  PRIMARY KEY (agent_id, card_hash)
);

-- 能力倒排索引
CREATE TABLE skills (
  agent_id     TEXT, skill_id TEXT, skill_version TEXT,
  tags         TEXT[], input_modes TEXT[], output_modes TEXT[]
);
CREATE INDEX idx_skills_lookup ON skills (skill_id, skill_version);
CREATE INDEX idx_skills_tags   ON skills USING GIN (tags);

-- 资源/合规属性（可变的过滤维度）
CREATE TABLE agent_attrs (
  agent_id TEXT PRIMARY KEY,
  compute  JSONB,   -- {"gpu":"4090","vram_gb":24,"region":"cn-east-2"}
  sla      JSONB,   -- {"max_latency_ms":2000,...}
  reputation_snapshot NUMERIC(4,3),  -- D5 快照，由 M8 事件更新
  updated_at TIMESTAMPTZ
);
CREATE INDEX idx_attrs_compute ON agent_attrs USING GIN (compute);
CREATE INDEX idx_attrs_active  ON agent_attrs (reputation_snapshot DESC)
  WHERE reputation_snapshot IS NOT NULL;
```

**在线状态不进 Postgres 的账目面**，只写可重建的在线表（设计形态：Redis `node:online:{id}`，TTL 90s）——高频写，且可重建。

> 更正（2026-09-14）：**当前实现里没有 Redis**。在线状态落在 `agents.last_seen_at`（SQLite），
> "只写 Redis"是**生产形态的设计意图**（见 `a2n_registry.service.set_online` 的 docstring：
> "在线状态由 Redis 承载；这里用 last_seen_at 近似"）。反向长连接枢纽 `a2n_transport.hub`
> 同样是进程内实现，代码里注明"生产换 Redis pub/sub"。

---

## 9. 记账单位：积分（point）

| 项 | 定义 |
|---|---|
| 单位 | **1 积分 = 0.01 元（1 分钱）**，固定汇率，v1 不浮动 |
| 存储 | 一律 **整数**（`delta_fen` / `amount_point`），**永不出现浮点数** |
| 产生 | 仅随充值（`deposit.confirmed` → mint），持牌方托管增加多少分，网内增加多少积分 |
| 销毁 | 仅随提现（持牌方打款回执 → burn） |
| 流转 | 网内消费、分账、持有者间转账——**只改账户归属，不改总量** |
| 分账 | 90/2/3/5 全部以积分计算，提现时持牌方按 1:1 兑付人民币 |
| 出口 | 只有提现一条。不做代币、不上链记账、不设二级市场 |
| 扩展位 | 未来若要"促销积分/版税点"等多类型，加 `point_type` 字段；**v1 保持单一类型**，避免复杂度 |

**不变式（红线测试）：** `SUM(ledger_entries.delta) == 持牌方托管余额（分）`，每日对账，差一分即冻结提现。

---

## 10. 与 ERC-8004 的关系

链上派系在做 agent 身份/信誉 registry（ERC-8004，已主网）。A2N 中心化注册表与之不冲突，但建议**数据结构可导出对齐**：

| ERC-8004 | A2N | 说明 |
|---|---|---|
| identity registry（agentId, agentURI, owner） | `agents` + `card_url` + `principal_id` | 一一对应 |
| reputation registry | `reputation_snapshot` + M8 | 可导出为标准格式 |
| validation registry | `verdicts`（M5） | 未来可做 TEE 验证 |

**做法：提供 `GET /v1/registry/export/erc8004` 导出接口。** 不接入链，但保证数据可迁移——将来若合规环境变化，主体与信誉记录能带走，不会被锁死。

---

## 11. 一句话

注册表的全部价值，在于它诚实地回答三个问题：**你是谁（签名 + KYC）、你能干什么（Card 自证）、你干得怎么样（信誉快照）**。

它不回答"你值多少钱"——那是市场的事，A2N 一辈子都不回答这个问题。
