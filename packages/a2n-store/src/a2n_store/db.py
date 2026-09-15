"""A2N 数据库与建表。

本地用 SQLite 保证开箱即跑；生产目标为 PostgreSQL（通过 adapters.persistence 切换）。
账本表在这里被施加 append-only 触发器 —— 廉洁性的第一道物理防线。
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

DB_PATH = os.environ.get("A2N_DB", str(Path(__file__).resolve().parents[4] / "data" / "a2n.db"))

_local = threading.local()

# 提交/回滚钩子：给"事务后动作"（事件通知延迟执行等）一个挂在提交点上的
# 接口。钩子同步执行、异常一律吞掉 —— 钩子失败不得影响事务本身。
_commit_hooks: list = []
_rollback_hooks: list = []


def on_commit(fn) -> None:
    """注册提交后回调（幂等：同一函数只挂一次）。"""
    if fn not in _commit_hooks:
        _commit_hooks.append(fn)


def on_rollback(fn) -> None:
    """注册回滚后回调（幂等）。"""
    if fn not in _rollback_hooks:
        _rollback_hooks.append(fn)


class _Conn(sqlite3.Connection):
    """带提交/回滚钩子的连接。

    事件通知延迟执行的关键一环：publish 背着事务时只排队不通知，
    本连接 commit() / rollback() 之后由钩子决定"发出去还是丢掉"。
    """

    def commit(self) -> None:
        super().commit()
        for fn in list(_commit_hooks):
            try:
                fn()
            except Exception:  # noqa: BLE001 - 钩子失败不得影响事务本身
                pass

    def rollback(self) -> None:
        super().rollback()
        for fn in list(_rollback_hooks):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB_PATH, check_same_thread=False, factory=_Conn)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        _local.conn = c
    return c


SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id           TEXT PRIMARY KEY,
  kind         TEXT NOT NULL,          -- user | node | author | fee | pool | hold
  name         TEXT NOT NULL,
  kyc_status   TEXT DEFAULT 'pending',
  created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger_entries (
  id          TEXT PRIMARY KEY,
  account_id  TEXT NOT NULL,
  delta       INTEGER NOT NULL,        -- 积分，整数；1 积分 = 0.01 元
  ref_type    TEXT NOT NULL,
  ref_id      TEXT NOT NULL,
  prev_hash   TEXT NOT NULL,
  hash        TEXT NOT NULL,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_account ON ledger_entries(account_id);

CREATE TABLE IF NOT EXISTS agents (
  agent_id      TEXT PRIMARY KEY,
  principal_id  TEXT NOT NULL,
  type          TEXT NOT NULL DEFAULT 'node',
  status        TEXT NOT NULL,
  kya_grade     TEXT DEFAULT 'C',
  visibility    TEXT NOT NULL DEFAULT 'public',   -- public | unlisted | private
  card_url      TEXT,
  card_hash     TEXT NOT NULL,
  card_json     TEXT NOT NULL,
  name          TEXT,
  compute       TEXT,                  -- JSON
  sla           TEXT,                  -- JSON
  price_hint    TEXT,                  -- JSON
  metering      TEXT,                  -- JSON
  reputation    REAL DEFAULT 0.5,
  tasks_done    INTEGER DEFAULT 0,
  earned        INTEGER DEFAULT 0,
  last_seen_at  TEXT,
  registered_at TEXT NOT NULL,
  connection    TEXT,                  -- JSON：连接模式（pull/wss/direct/relay）与可达性
  peer_ip       TEXT,                  -- 平台观测到的节点出口 IP，用于 NAT 判定
  uid           TEXT                   -- 网络唯一标识（UUID）：供给方生成，平台背书唯一
);

CREATE TABLE IF NOT EXISTS skills (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id      TEXT NOT NULL,
  skill_id      TEXT NOT NULL,
  skill_version TEXT NOT NULL DEFAULT '1.0.0',
  tags          TEXT,
  input_modes   TEXT
);
CREATE INDEX IF NOT EXISTS idx_skills_lookup ON skills(skill_id, agent_id);

CREATE TABLE IF NOT EXISTS tasks (
  id           TEXT PRIMARY KEY,      -- 规范任务主键：执行事实的唯一身份
  requester_id TEXT NOT NULL,
  skill_id     TEXT NOT NULL,
  source       TEXT NOT NULL DEFAULT 'native',  -- native（平台派单）| a2a（标准协议入口）
  payload_hash TEXT,
  payload      TEXT,
  budget       INTEGER NOT NULL,
  unit_prices  TEXT NOT NULL,          -- JSON 锁定的合约价
  card_hash    TEXT,                   -- 接单时的 card 快照，防事后涨价
  state        TEXT NOT NULL,
  node_id      TEXT,
  delivery     TEXT NOT NULL DEFAULT 'dispatch',  -- dispatch 节点取活 | inline 随调用就地交付
  result_hash  TEXT,
  result       TEXT,
  amount       INTEGER DEFAULT 0,
  -- 多币种（S1 双写）：currency 是这笔任务的计价/结算币种（计价币种=结算币种）；
  -- amount_minor 是金额的整数最小单位（10^-exponent），exponent 归媒介注册表。
  -- amount（分）继续写，读取方 COALESCE(amount_minor, amount) —— 老数据不断供。
  currency     TEXT DEFAULT 'CNY',
  amount_minor INTEGER,
  budget_minor INTEGER,
  reject_reason TEXT,                 -- 验收不通过的原因（A2A 视角为 failed）
  fail_reason   TEXT,                 -- 执行失败 / 取消的原因
  -- 试用标：1 = 这一单落在该 agent 的免费试用额度内（不按人计，完成的调用即计数）。
  -- 免费单天然落在 ACCEPTED（非积分完成），不打标就会把"付费成功率"抬高 ——
  -- 统计口径必须按它隔离（成功率剔除、GMV 本就不计）。
  trial        INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_reports (
  usage_id     TEXT PRIMARY KEY,
  task_id      TEXT NOT NULL,
  node_id      TEXT NOT NULL,
  dims         TEXT NOT NULL,          -- 节点自报（内部真相）
  observed     TEXT,                   -- 平台侧观测（外部真相）
  variance     REAL,
  result_hash  TEXT,
  contract     TEXT,
  signature    TEXT,
  status       TEXT DEFAULT 'pending',
  -- 计量签名（a2n-p2p.attest）：验签过的计量才是**可复算的证据**。
  -- attested=1 才敢说"这是节点签的"；attested=0 只能说"平台观测 vs 节点自报比对过"。
  -- 签名错的计量进 disputed，不进 reconciled —— 只给结论不给过程的"可信"是假的。
  attest        TEXT,                  -- JSON：{did,pub,sig,payload}
  attested      INTEGER DEFAULT 0,
  attest_reason TEXT,
  -- 模板偏差（质量硬指标）：quality = 100 × (1 − D)；无模板时为 NULL（绝不许伪造 0 偏差）
  quality       REAL,
  template_ref  TEXT,                  -- JSON：{key,version,weights} 当时的模板口径
  deviation     TEXT,                  -- JSON：{d_struct,d_completeness,d_content,D,...}
  created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settlement_orders (
  id            TEXT PRIMARY KEY,
  task_id       TEXT NOT NULL,
  amount        INTEGER NOT NULL,
  splits        TEXT NOT NULL,
  rule_ref      TEXT NOT NULL,
  custodian_ref TEXT,
  state         TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  -- 多币种（S1 双写）：分账指令与任务同币种（计价币种=结算币种）
  currency      TEXT DEFAULT 'CNY',
  amount_minor  INTEGER
);

CREATE TABLE IF NOT EXISTS withdrawals (
  id            TEXT PRIMARY KEY,
  account_id    TEXT NOT NULL,
  amount        INTEGER NOT NULL,
  state         TEXT NOT NULL,
  custodian_ref TEXT,
  created_at    TEXT NOT NULL,
  -- 多币种（S1 双写）：一期积分账本锚 CNY 分，二期按媒介扩
  currency      TEXT DEFAULT 'CNY',
  amount_minor  INTEGER
);

CREATE TABLE IF NOT EXISTS receipts (
  seq        INTEGER PRIMARY KEY AUTOINCREMENT,
  rid        TEXT UNIQUE,
  event_type TEXT NOT NULL,
  payload    TEXT NOT NULL,
  prev_hash  TEXT NOT NULL,
  hash       TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox (
  id         TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  payload    TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rosters (
  principal_id TEXT PRIMARY KEY,   -- 我的市场列表（演示用服务端镜像）
  roster       TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);

-- 共识 epoch：一个区间的账目压成一个 Merkle 根，集齐见证与资金锚才算数
CREATE TABLE IF NOT EXISTS epochs (
  epoch_id            TEXT PRIMARY KEY,
  seq                 INTEGER NOT NULL,
  from_rowid          INTEGER NOT NULL,     -- 账本区间起点（不含）
  to_rowid            INTEGER NOT NULL,     -- 区间终点（含）
  entry_count         INTEGER NOT NULL,
  merkle_root         TEXT NOT NULL,
  prev_hash           TEXT NOT NULL,        -- 串联上一个 epoch，构成 epoch 链
  epoch_hash          TEXT NOT NULL,
  escrow_balance_fen  INTEGER DEFAULT 0,    -- 封口时刻的资金锚读数
  state               TEXT NOT NULL,        -- OPEN | SEALED | ANCHORED
  created_at          TEXT NOT NULL,
  sealed_at           TEXT,
  anchored_at         TEXT
);

CREATE TABLE IF NOT EXISTS epoch_sigs (
  id           TEXT PRIMARY KEY,
  epoch_id     TEXT NOT NULL,
  signer       TEXT NOT NULL,
  kind         TEXT NOT NULL,               -- witness | anchor
  sig          TEXT NOT NULL,
  attestation  TEXT,                        -- 资金锚声明（仅 kind=anchor）
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_epoch_sigs ON epoch_sigs(epoch_id, kind);

-- 争议：验收不通过的双向计量对账，交给人裁决
CREATE TABLE IF NOT EXISTS disputes (
  id           TEXT PRIMARY KEY,
  task_id      TEXT NOT NULL,
  opened_by    TEXT NOT NULL,               -- requester | node | system
  side         TEXT NOT NULL,
  reason       TEXT NOT NULL,
  evidence     TEXT,                        -- JSON：自报计量 / 平台观测 / 凭证
  state        TEXT NOT NULL,               -- OPEN | RESOLVED
  ruling       TEXT,                        -- uphold_reject | overturn_pay | partial
  refund_fen   INTEGER DEFAULT 0,
  arbitrator   TEXT,
  resolution   TEXT,
  created_at   TEXT NOT NULL,
  resolved_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_disputes_task ON disputes(task_id);

-- 持牌方托管账（Mock）：只有充值与打款改变余额，分账指令只在托管内部改归属
CREATE TABLE IF NOT EXISTS custodian_book (
  id         TEXT PRIMARY KEY,
  kind       TEXT NOT NULL,            -- deposit | payout | settlement | payin
  detail     TEXT,
  amount     INTEGER NOT NULL,         -- 该币种的整数最小单位
  ref        TEXT,
  created_at TEXT NOT NULL,
  -- 多币种：托管账按币种单列。对账只锚 CNY 口径（积分账本锚 CNY），
  -- USDC 等通道的扣款按各自币种记，不许当 CNY 分累加。
  currency   TEXT DEFAULT 'CNY'
);

-- ============ 一期：账户与对等账户（不涉及资金，只建立交易关系） ============
-- 注意区分：上面的 accounts 是二期积分账本的记账科目；
-- 这里的 party_accounts 是"一个主体持有的多个结算账户"，一期只做关系与对账。
CREATE TABLE IF NOT EXISTS party_accounts (
  account_id   TEXT PRIMARY KEY,
  owner_id     TEXT NOT NULL,          -- 主体（principal_id / DID）
  label        TEXT NOT NULL,          -- 备注名，如"对公-研发线"、"海外-Wise"
  ref          TEXT,                   -- 外部账户标识（对公账号/钱包/内部编号），A2N 不校验只标注
  status       TEXT NOT NULL DEFAULT 'ACTIVE',   -- ACTIVE | FROZEN | CLOSED
  created_at   TEXT NOT NULL,
  -- 多媒介结算账户（媒介注册表：a2n-custodian.media）：
  -- medium/currency 必须是注册表里已注册的 code（加媒介 = 注册一条，不改代码）。
  -- exponent 从注册表抄进来**冻结成事实**——注册表日后改指数，历史账户的
  -- 金额解释不能跟着变。network/address 只对链上媒介有意义，A2N 不校验。
  medium       TEXT DEFAULT 'channel_pay',
  currency     TEXT DEFAULT 'CNY',
  exponent     INTEGER DEFAULT 2,
  network      TEXT,
  address      TEXT,
  direction    TEXT DEFAULT 'both',    -- pay 付出 | receive 收款 | both
  is_default   INTEGER DEFAULT 0       -- 同主体同币种的默认结算账户
);
CREATE INDEX IF NOT EXISTS idx_party_accounts_owner ON party_accounts(owner_id);

-- 对等账户：使用方账户 ↔ agent 的配对关系（条款谈妥才能交易）
CREATE TABLE IF NOT EXISTS peer_links (
  link_id      TEXT PRIMARY KEY,
  account_id   TEXT NOT NULL,
  agent_id     TEXT NOT NULL,
  peer_ref     TEXT,                   -- 对端账户标识（agent 侧给的收款账户/对账编号）
  terms        TEXT NOT NULL,          -- JSON: unit_prices / billing_cycle / net_days / credit_limit_fen
  state        TEXT NOT NULL,          -- PROPOSED | ACTIVE | SUSPENDED | CLOSED
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_peer_links_pair ON peer_links(account_id, agent_id);
CREATE INDEX IF NOT EXISTS idx_peer_links_account ON peer_links(account_id);

-- 一期交易：达成 → 交付 → 双向计量 → 对账 → 出账。全程不碰钱。
CREATE TABLE IF NOT EXISTS deals (
  deal_id      TEXT PRIMARY KEY,
  link_id      TEXT NOT NULL,
  account_id   TEXT NOT NULL,
  agent_id     TEXT NOT NULL,
  skill        TEXT NOT NULL,
  terms        TEXT NOT NULL,          -- 条款快照：成交那一刻冻结，事后改条款无效
  state        TEXT NOT NULL,          -- DRAFT | AGREED | DELIVERED | RECONCILED | DISPUTED | CLOSED | CANCELED
  task_id      TEXT,                   -- 可选：挂到 a2n-task 的执行实例上
  statement_id TEXT,                   -- 已出账后回填，避免重复出账
  opened_at    TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deals_link ON deals(link_id);

-- 双向计量：双方各自上报，A2N 只记录事实，不替任何一方下结论
CREATE TABLE IF NOT EXISTS deal_reports (
  id           TEXT PRIMARY KEY,
  deal_id      TEXT NOT NULL,
  party        TEXT NOT NULL,          -- requester | provider
  dims         TEXT NOT NULL,          -- JSON 计量维度（时长/token/调用…）
  amount_fen   INTEGER,                -- 该方自报的应付金额（分）
  evidence     TEXT,                   -- JSON：结果哈希等可验证材料
  reported_at  TEXT NOT NULL,
  currency     TEXT DEFAULT 'CNY',     -- 自报金额的币种（来自条款/显式指定）
  amount_minor INTEGER               -- 同额的整数最小单位值（S1 双写）
);
CREATE INDEX IF NOT EXISTS idx_deal_reports_deal ON deal_reports(deal_id);

CREATE TABLE IF NOT EXISTS deal_recons (
  deal_id      TEXT PRIMARY KEY,
  amount_fen   INTEGER NOT NULL,       -- 对账认定的金额（分）
  delta_fen    INTEGER NOT NULL,       -- 双方差异
  matched      INTEGER NOT NULL,       -- 1 一致 / 0 有差异
  policy       TEXT,
  created_at   TEXT NOT NULL,
  currency     TEXT DEFAULT 'CNY',     -- 认定金额的币种
  amount_minor INTEGER,
  delta_minor  INTEGER
);

-- A2A v1.0 任务视图：对外说 A2A 的话，内部仍走 A2N 的验收与记账
-- A2A 协议适配视图：**不是第二份事实**。
-- task_id 就是规范任务主键（1:1 挂 tasks.id），A2A 客户端看到的 Task id 也用它——
-- 一个 id、一份事实。这里只存 A2A 专有字段（contextId / message / artifacts）；
-- 状态一律以 tasks.state 为准，协议侧转成 A2A TaskState，杜绝两套状态各说各话。
CREATE TABLE IF NOT EXISTS a2a_tasks (
  task_id      TEXT PRIMARY KEY,
  agent_id     TEXT NOT NULL,
  context_id   TEXT,
  skill        TEXT,
  message      TEXT NOT NULL,          -- 原始 A2A Message JSON
  artifacts    TEXT,                   -- JSON：结果 artifacts
  error        TEXT,
  deal_id      TEXT,                   -- 对等账户结算时挂的交易
  charge_id    TEXT,                   -- 直付/x402 结算时挂的凭证
  settle_mode  TEXT,                   -- 结算方式快照（capability token，如 direct_pay:渠道）
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_a2a_tasks_agent ON a2a_tasks(agent_id);

-- 使用方登记的支付方式（直付类结算的"我加个支付渠道"）。A2N 不碰钱、不校验
-- 真伪、不认识品牌——channel 是自由字符串，品牌知识归持牌托管层，业务层
-- 只认 direct_pay + 渠道字符串。
CREATE TABLE IF NOT EXISTS payment_methods (
  pm_id        TEXT PRIMARY KEY,
  principal_id TEXT NOT NULL,         -- 登记主体
  method       TEXT NOT NULL,         -- direct_pay（通用直付模式）
  channel      TEXT NOT NULL,         -- 渠道限定符（自由字符串，品牌归持牌层）
  ref          TEXT,                  -- 外部账户标识（手机号尾号/支付ID），A2N 不校验
  status       TEXT NOT NULL DEFAULT 'ACTIVE',   -- ACTIVE | CLOSED
  created_at   TEXT NOT NULL,
  medium       TEXT DEFAULT 'channel_pay',  -- 结算媒介（注册表 code）
  currency     TEXT DEFAULT 'CNY'           -- 该渠道的结算币种
);
CREATE INDEX IF NOT EXISTS idx_pm_principal ON payment_methods(principal_id, method, channel);

-- 直付成交凭证。钱走外部渠道，账走 A2N——这里存的是**可机器验证的合约**：
-- 单价快照（成交时点冻结）+ 计量 + 金额 + AP2 授权链 + 任务关联。
-- 与对等账户的 deals 平行，各管各的结算语义，不混用一张表。
--
-- 状态机（不许撒谎）：AUTHORIZED = 已授权/已记账但**未确认到账**；
-- CAPTURED = 渠道或托管回执确认后才有资格叫。没有回执就是 AUTHORIZED。
CREATE TABLE IF NOT EXISTS pay_charges (
  charge_id    TEXT PRIMARY KEY,
  agent_id     TEXT NOT NULL,
  principal_id TEXT NOT NULL,         -- 付款方
  method       TEXT NOT NULL,         -- direct_pay（通用直付模式）/ x402（微支付）
  channel      TEXT NOT NULL,         -- 渠道限定符（成交时点冻结）
  pm_id        TEXT,                  -- 用的哪张登记支付方式（x402 即付无需登记）
  skill        TEXT NOT NULL,
  unit_price_fen INTEGER NOT NULL,    -- 单价快照：card price_hint 成交时点冻结
  call_count   INTEGER NOT NULL DEFAULT 1,
  amount_fen   INTEGER NOT NULL,      -- unit_price_fen × call_count
  state        TEXT NOT NULL,         -- AUTHORIZED | CAPTURED | FAILED
  -- 多币种（S1 双写）：成交那一刻的币种与整数最小单位金额（快照，永不变）
  currency     TEXT DEFAULT 'CNY',
  medium       TEXT,
  unit_price_minor INTEGER,
  amount_minor INTEGER,
  mandate_chain TEXT NOT NULL,        -- JSON：intent/cart/payment 三凭证全文
  task_id      TEXT,                  -- 关联规范任务主键
  channel_ref  TEXT,                  -- 渠道侧流水号（回执回调时回填）
  authorized_at TEXT,                 -- 授权/记账时刻
  captured_at  TEXT,                  -- 回执确认时刻
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_charges_principal ON pay_charges(principal_id, method, channel);
CREATE INDEX IF NOT EXISTS idx_charges_agent ON pay_charges(agent_id);
CREATE INDEX IF NOT EXISTS idx_charges_task ON pay_charges(task_id);

CREATE TABLE IF NOT EXISTS statements (
  statement_id TEXT PRIMARY KEY,
  link_id      TEXT NOT NULL,
  period       TEXT NOT NULL,          -- 账期，如 2026-09
  total_fen    INTEGER NOT NULL,
  deal_count   INTEGER NOT NULL,
  state        TEXT NOT NULL,          -- OPEN | ISSUED | SETTLED（SETTLED 属二期：托管商实际划转后）
  created_at   TEXT NOT NULL,
  currency     TEXT DEFAULT 'CNY',     -- 账单币种：一张账单一种币，混币不出单
  total_minor  INTEGER               -- 同额的整数最小单位值（S1 双写）
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_statements_period ON statements(link_id, period);

-- ============ 质量证据：使用评价 + 试用与毕业（硬指标与软评分分开，绝不合成总分）============
-- 使用评价：**一条评分绑定一次具体交付**（task_id 唯一）。绑定交付有两个硬好处：
-- ① 每个评分都能点开看那次交付，评价才可解释；② 凭空打分没入口，天然防刷。
-- raw_score    使用者原始分（0..100）
-- normalized   归一化后对外分；mu_used / rater_n 一起存档，使归一化**可复算**
--              （f(x) 两段折线，锚点 μ = 该评分者给所有人的平均分的收缩值）
-- self_source  1 = 同源（agent 主人自己评自己）：不进公开证据、不进统计
-- credited     1 = 计入对外统计（非自源 且 评分者已过最小样本门）
-- trial        1 = 这一单处于试用期（案例站"试用"标）
CREATE TABLE IF NOT EXISTS ratings (
  rating_id    TEXT PRIMARY KEY,
  task_id      TEXT NOT NULL UNIQUE,
  agent_id     TEXT NOT NULL,
  rater_id     TEXT NOT NULL,
  skill        TEXT,
  raw_score    INTEGER NOT NULL,
  normalized   REAL NOT NULL,
  mu_used      REAL NOT NULL,
  rater_n      INTEGER NOT NULL,
  self_source  INTEGER NOT NULL DEFAULT 0,
  credited     INTEGER NOT NULL DEFAULT 0,
  trial        INTEGER NOT NULL DEFAULT 0,
  note         TEXT,
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ratings_agent ON ratings(agent_id, credited);
CREATE INDEX IF NOT EXISTS idx_ratings_rater ON ratings(rater_id);

-- 试用额度与毕业：新 agent 的前 N 次**完成**的调用免费（**不按人计** —— 提供者
-- 做满 10 次活儿却一分钱没有，还要被要求"来自不同人"，体感就是被白嫖）。
-- 代价（自己调自己也能凑满）不靠加限制消除，而靠"计数"与"证据"分开算：
-- 自源调用照常吃额度，但**不进公开案例、不进评分统计**（见 ratings.self_source）。
-- 表归属 a2n-registry（agent 生命周期）。
CREATE TABLE IF NOT EXISTS trial_offers (
  agent_id       TEXT PRIMARY KEY,
  state          TEXT NOT NULL DEFAULT 'TRIAL',   -- TRIAL | GRADUATED
  used           INTEGER NOT NULL DEFAULT 0,      -- 当前额度已用（完成的调用）
  cap            INTEGER NOT NULL DEFAULT 10,     -- 当前额度上限（首装 10，重连补 5）
  total_used     INTEGER NOT NULL DEFAULT 0,      -- 生命周期累计已用
  granted_total  INTEGER NOT NULL DEFAULT 10,     -- 生命周期累计授予（有总上限）
  last_grant_at  TEXT,                            -- 最近一次补额时刻（每自然日最多 1 次）
  graduated_at   TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

-- 结算收口（P1 §3.2）：把"两条记账路"（积分 / 对等账户 / 直付 / x402）的**结果**
-- 收进同一张表，task_id 作幂等键 —— 一单只结一次，重复事件不许二次结算。
-- 失败进 PENDING 而不是消失：结算是钱的事，静默失败比失败本身更危险。
-- 表归属 a2n-settlement（closing.py）。
CREATE TABLE IF NOT EXISTS settlements (
  task_id      TEXT PRIMARY KEY,       -- 幂等键：一单只结一次
  mode         TEXT NOT NULL,          -- prepaid_points / peer_account / direct_pay:<渠道> / x402 / free
  amount_minor INTEGER NOT NULL DEFAULT 0,
  currency     TEXT NOT NULL DEFAULT 'CNY',
  ref          TEXT,                   -- 凭据号：so_id / deal_id / charge_id
  state        TEXT NOT NULL,          -- SETTLED（已结）| PENDING（待处理）| FAILED（结不动）
  reason       TEXT,                   -- 待处理/失败的原因（要能点开看到是哪一单为什么）
  attempts     INTEGER NOT NULL DEFAULT 1,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_settlements_state ON settlements(state);
CREATE INDEX IF NOT EXISTS idx_settlements_updated ON settlements(updated_at);

-- 日切对账（P1 §3.2）：每日一次「积分总量 ≡ 托管余额」，结果落库留痕。
-- 只存结论与差异，不存明细账（明细账在 ledger_entries / custodian_book）。
-- day 唯一：同一天重跑是**覆盖**而不是追加 —— 追加会把"每天几次"变成一条假历史。
CREATE TABLE IF NOT EXISTS reconciliations (
  id                 TEXT PRIMARY KEY,
  day                TEXT NOT NULL UNIQUE,   -- 日切按天唯一（YYYY-MM-DD）
  points_total       INTEGER NOT NULL,
  escrow_balance_fen INTEGER NOT NULL,
  diff               INTEGER NOT NULL,       -- 积分 − 托管（非 0 = 冻结提现并告警）
  balanced           INTEGER NOT NULL,
  due_count          INTEGER NOT NULL DEFAULT 0,   -- 当日应结（验收通过）
  settled_count      INTEGER NOT NULL DEFAULT 0,   -- 当日已结
  pending_count      INTEGER NOT NULL DEFAULT 0,   -- 待处理（含跨日未结）
  note               TEXT,
  created_at         TEXT NOT NULL
);
"""

TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger_entries
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: UPDATE forbidden'); END;
CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger_entries
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: DELETE forbidden'); END;
CREATE TRIGGER IF NOT EXISTS receipts_no_update BEFORE UPDATE ON receipts
BEGIN SELECT RAISE(ABORT, 'receipt chain is append-only'); END;
-- 签名只增不改：改签名等于改历史，追责链就断了
CREATE TRIGGER IF NOT EXISTS epoch_sigs_no_update BEFORE UPDATE ON epoch_sigs
BEGIN SELECT RAISE(ABORT, 'epoch signatures are append-only'); END;
CREATE TRIGGER IF NOT EXISTS epoch_sigs_no_delete BEFORE DELETE ON epoch_sigs
BEGIN SELECT RAISE(ABORT, 'epoch signatures are append-only'); END;
"""

# 流水 = 对已有事实的只读聚合视图（不建新表、不造第二份事实）。
# 来源：pay_charges（直付/x402 成交）+ statements（对等账单）。
# 每笔事实拆成付/收两行：使用方看 out，提供方看 in —— 金额相等、方向相反，
# 一笔钱在两边都能对上。金额读取 COALESCE(amount_minor, *_fen)：
# 老数据只写了分，新数据双写 —— 视图永远给"整数最小单位"口径。
# 引用新列的索引/视图都在这里：必须先由 _ensure_columns 补齐老库缺列
# 之后才能执行（SQLite 建索引/视图时就会解析列）。
POST_MIGRATE = """
CREATE INDEX IF NOT EXISTS idx_party_accounts_currency ON party_accounts(owner_id, currency);

-- 网络唯一标识：UUID 级全网唯一，平台背书（同 uid 二次注册直接拒绝）。
-- SQLite UNIQUE 允许多个 NULL，旧库未补录 uid 的存量节点不冲突。
CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_uid ON agents(uid);

CREATE VIEW IF NOT EXISTS v_account_ledger AS
SELECT pc.principal_id AS owner_id, 'out' AS direction,
       pc.agent_id AS counterparty,
       pc.method || COALESCE(':' || pc.channel, '') AS channel,
       'direct_charge' AS kind, pc.charge_id AS ref_id,
       COALESCE(pc.currency, 'CNY') AS currency,
       COALESCE(pc.amount_minor, pc.amount_fen) AS amount_minor,
       pc.amount_fen AS amount_fen,
       pc.state AS state, pc.skill AS note,
       COALESCE(pc.authorized_at, pc.created_at) AS happened_at
FROM pay_charges pc
UNION ALL
SELECT pc.agent_id, 'in', pc.principal_id,
       pc.method || COALESCE(':' || pc.channel, ''),
       'direct_charge', pc.charge_id,
       COALESCE(pc.currency, 'CNY'),
       COALESCE(pc.amount_minor, pc.amount_fen),
       pc.amount_fen, pc.state, pc.skill,
       COALESCE(pc.authorized_at, pc.created_at)
FROM pay_charges pc
UNION ALL
SELECT pa.owner_id, 'out', pl.agent_id,
       'peer_account', 'statement', s.statement_id,
       COALESCE(s.currency, 'CNY'),
       COALESCE(s.total_minor, s.total_fen),
       s.total_fen, s.state, s.period, s.created_at
FROM statements s
JOIN peer_links pl ON pl.link_id = s.link_id
JOIN party_accounts pa ON pa.account_id = pl.account_id
UNION ALL
SELECT pl.agent_id, 'in', pa.owner_id,
       'peer_account', 'statement', s.statement_id,
       COALESCE(s.currency, 'CNY'),
       COALESCE(s.total_minor, s.total_fen),
       s.total_fen, s.state, s.period, s.created_at
FROM statements s
JOIN peer_links pl ON pl.link_id = s.link_id
JOIN party_accounts pa ON pa.account_id = pl.account_id;
"""


@contextmanager
def tx():
    """显式写事务（BEGIN IMMEDIATE）：把"读-判-写"串成一个原子动作。

    SQLite WAL 下 IMMEDIATE 立刻拿写锁，并发的第二个写被挡在外面排队，
    而不是各自读到同一份旧值再一起写穿 —— 余额、额度、状态推进这类
    地方必须用它，否则两个请求可以同时"余额够"然后一起超提。

    参与事务的代码必须把 commit 交出来（如 Ledger.post(commit=False)），
    否则内部的 commit 会提前提交事务、锁随之释放，等于没包。
    """
    c = conn()
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
    except BaseException:
        c.rollback()
        raise
    else:
        c.commit()


def init_db() -> None:
    c = conn()
    c.executescript(SCHEMA)
    c.executescript(TRIGGERS)
    _ensure_columns()
    c.executescript(POST_MIGRATE)   # 引用新列的索引与视图，必须在补列之后建
    c.commit()


def _ensure_columns() -> None:
    """轻量迁移：已有库缺列时补齐。

    只做"加列"这一件事：加列是向后兼容的（旧代码不读新列也能跑），
    而改列/删列不是。真要改语义就上新表 + 迁移脚本，别在这里做手术。
    """
    c = conn()

    def cols_of(table: str) -> set[str]:
        return {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}

    def add(table: str, col: str, decl: str) -> None:
        if col not in cols_of(table):
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    for col, decl in (("connection", "TEXT"), ("peer_ip", "TEXT"), ("uid", "TEXT")):
        add("agents", col, decl)
    for col, decl in (("reject_reason", "TEXT"), ("fail_reason", "TEXT"),
                      # 任务来源：native（平台派单）/ a2a（标准协议入口）
                      ("source", "TEXT NOT NULL DEFAULT 'native'"),
                      # 试用标：1 = 落在该 agent 的免费试用额度内（统计口径按它隔离）
                      ("trial", "INTEGER NOT NULL DEFAULT 0"),
                      # 任务投递：dispatch（节点取活：推送+长轮询）| inline（随调用就地交付）
                      ("delivery", "TEXT NOT NULL DEFAULT 'dispatch'")):
        add("tasks", col, decl)
    # 计量签名与模板偏差（老库补列）：attested=1 才敢说"这是节点签的"
    for col, decl in (("attest", "TEXT"), ("attested", "INTEGER DEFAULT 0"),
                      ("attest_reason", "TEXT"), ("quality", "REAL"),
                      ("template_ref", "TEXT"), ("deviation", "TEXT")):
        add("usage_reports", col, decl)
    add("deals", "statement_id", "TEXT")
    # A2A 协议视图：结算方式快照（读 metadata 用）
    add("a2a_tasks", "settle_mode", "TEXT")
    # 直付状态机：AUTHORIZED（已授权/已记录）→ CAPTURED（渠道回执确认）
    for col, decl in (("authorized_at", "TEXT"), ("captured_at", "TEXT"),
                      ("channel_ref", "TEXT")):
        add("pay_charges", col, decl)
    # 仲裁退款多币种：refund_fen 是 CNY 分的旧口径，最小单位与币种双写补齐
    for col, decl in (("refund_minor", "INTEGER"), ("refund_currency", "TEXT")):
        add("disputes", col, decl)
    # ===== 多币种 S1 补列（老库补齐后 v_account_ledger 才能建）=====
    # 金额双写：amount_minor（整数最小单位）与旧 *_fen 并存，读方 COALESCE。
    # currency 带默认值：老代码不认识币种时，如实记为当时的体系默认（CNY 分）。
    for col, decl in (("currency", "TEXT DEFAULT 'CNY'"), ("amount_minor", "INTEGER"),
                      ("budget_minor", "INTEGER")):
        add("tasks", col, decl)
        add("settlement_orders", col, decl)
        add("withdrawals", col, decl)
        add("deal_reports", col, decl)
    for col, decl in (("currency", "TEXT DEFAULT 'CNY'"), ("amount_minor", "INTEGER"),
                      ("delta_minor", "INTEGER")):
        add("deal_recons", col, decl)
    for col, decl in (("currency", "TEXT DEFAULT 'CNY'"), ("total_minor", "INTEGER")):
        add("statements", col, decl)
    # party_accounts：多媒介结算账户（medium/currency 是注册表 code，exponent
    # 从注册表抄进来冻结成事实，注册表日后改指数不影响历史账户的解释）
    for col, decl in (("medium", "TEXT DEFAULT 'channel_pay'"),
                      ("currency", "TEXT DEFAULT 'CNY'"),
                      ("exponent", "INTEGER DEFAULT 2"),
                      ("network", "TEXT"), ("address", "TEXT"),
                      ("direction", "TEXT DEFAULT 'both'"),
                      ("is_default", "INTEGER DEFAULT 0")):
        add("party_accounts", col, decl)
    for col, decl in (("medium", "TEXT DEFAULT 'channel_pay'"),
                      ("currency", "TEXT DEFAULT 'CNY'")):
        add("payment_methods", col, decl)
    for col, decl in (("currency", "TEXT DEFAULT 'CNY'"), ("medium", "TEXT"),
                      ("unit_price_minor", "INTEGER"), ("amount_minor", "INTEGER")):
        add("pay_charges", col, decl)
    # 托管账按币种单列：老库的历史流水都是当时体系的默认口径（CNY 分），
    # 补列时如实记为 CNY；此后 x402 等通道的扣款按各自币种记。
    add("custodian_book", "currency", "TEXT DEFAULT 'CNY'")
