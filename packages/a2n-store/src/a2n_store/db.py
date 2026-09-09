"""A2N 数据库与建表。

本地用 SQLite 保证开箱即跑；生产目标为 PostgreSQL（通过 adapters.persistence 切换）。
账本表在这里被施加 append-only 触发器 —— 廉洁性的第一道物理防线。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

DB_PATH = os.environ.get("A2N_DB", str(Path(__file__).resolve().parents[4] / "data" / "a2n.db"))

_local = threading.local()


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB_PATH, check_same_thread=False)
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
  peer_ip       TEXT                   -- 平台观测到的节点出口 IP，用于 NAT 判定
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
  kind       TEXT NOT NULL,            -- deposit | payout | settlement
  detail     TEXT,
  amount     INTEGER NOT NULL,         -- 分（人民币分）
  ref        TEXT,
  created_at TEXT NOT NULL
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
CREATE INDEX IF NOT EXISTS idx_party_accounts_currency ON party_accounts(owner_id, currency);

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
# 视图引用的新列必须先由 _ensure_columns 补齐后才能建（SQLite 建视图时
# 会解析列），所以视图单独放 VIEWS，在 init_db 里最后执行。
VIEWS = """
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


def init_db() -> None:
    c = conn()
    c.executescript(SCHEMA)
    c.executescript(TRIGGERS)
    _ensure_columns()
    c.executescript(VIEWS)   # 视图引用新列，必须在补列之后建
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

    for col, decl in (("connection", "TEXT"), ("peer_ip", "TEXT")):
        add("agents", col, decl)
    for col, decl in (("reject_reason", "TEXT"), ("fail_reason", "TEXT"),
                      # 任务来源：native（平台派单）/ a2a（标准协议入口）
                      ("source", "TEXT NOT NULL DEFAULT 'native'"),
                      # 任务投递：dispatch（节点取活：推送+长轮询）| inline（随调用就地交付）
                      ("delivery", "TEXT NOT NULL DEFAULT 'dispatch'")):
        add("tasks", col, decl)
    add("deals", "statement_id", "TEXT")
    # A2A 协议视图：结算方式快照（读 metadata 用）
    add("a2a_tasks", "settle_mode", "TEXT")
    # 直付状态机：AUTHORIZED（已授权/已记录）→ CAPTURED（渠道回执确认）
    for col, decl in (("authorized_at", "TEXT"), ("captured_at", "TEXT"),
                      ("channel_ref", "TEXT")):
        add("pay_charges", col, decl)
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
