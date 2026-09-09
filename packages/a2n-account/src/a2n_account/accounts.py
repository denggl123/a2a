"""a2n-account（L2）：账户与对等账户。

一期定位：**建立交易关系，不碰资金。**

一个主体（owner）可以持有多个账户——对公、对私、海外、不同业务线各一个。
账户本身不是钱包，A2N 不存钱、不托管、不校验外部账户真伪，只记录
"这个主体声明自己有这么一个账户，并愿意用它来结算"。

对等账户（peer link）是使用方账户与某个 agent 之间的配对关系：
条款谈妥（单价、账期、额度）并双方确认后，这对账户之间才能达成交易。
**没有配对就没有交易**——这是一期把"陌生人交易"变成"熟人交易"的机制。
"""
from __future__ import annotations

import json
from typing import Any

from a2n_kernel import new_id, now_iso, publish
from a2n_store import conn

# agent 在 card 里声明自己接受的结算方式
PEER_ACCOUNT = "peer_account"        # 一期：对等账户，双边记账、按账期对账结清
PREPAID_POINTS = "prepaid_points"    # 二期：托管商发行积分，预冻结后按实结算
DIRECT_PAY = "direct_pay"            # 直付：第三方渠道即时扣款。A2N 不碰资金流，
                                     # 只记授权与凭证。具体渠道是数据不是代码——
                                     # 写成限定符 direct_pay:<渠道>，品牌知识归
                                     # 持牌托管层（a2n-custodian），业务层永远
                                     # 不认识任何支付品牌（铁律二的代码投影）。
X402 = "x402"                        # 微支付：x402 协议，请求即付（HTTP 402 + 支付凭证）
                                     # 无需预先建立任何关系——带凭证来就能调，
                                     # 小额高频场景的天然解。校验与扣款走持牌层。
SETTLE_MODES = (PEER_ACCOUNT, PREPAID_POINTS, DIRECT_PAY, X402)

DEFAULT_TERMS: dict[str, Any] = {
    "unit_prices": {},        # 计量维度 → 单价。如 {"call_count": 3} 或 v2 条目列表
    "currency": "CNY",        # 条款结算币种（计价币种=结算币种，成交时冻结）
    "billing_cycle": "monthly",
    "net_days": 30,           # 出账后多少天内结清
    "credit_limit_fen": 0,    # 0 = 不限额；>0 = 该配对下未结金额上限
}

STATE_PROPOSED = "PROPOSED"
STATE_ACTIVE = "ACTIVE"
STATE_SUSPENDED = "SUSPENDED"
STATE_CLOSED = "CLOSED"

NEXT_STATE = {
    STATE_PROPOSED: {STATE_ACTIVE, STATE_CLOSED},
    STATE_ACTIVE: {STATE_SUSPENDED, STATE_CLOSED},
    STATE_SUSPENDED: {STATE_ACTIVE, STATE_CLOSED},
    STATE_CLOSED: set(),
}


def merge_terms(given: dict | None) -> dict:
    """条款合并：只认已知字段，未知字段不进库（防止塞进无法执行的条款）。"""
    out = dict(DEFAULT_TERMS)
    for k in DEFAULT_TERMS:
        if given and k in given:
            out[k] = given[k]
    return out


class Accounts:
    """主体持有的多个结算账户。

    账户带媒介与币种（媒介注册表：a2n-custodian.media）。业务层只认
    medium/currency 的 code 字符串，不认识任何支付品牌——加一种支付方式
    = 持牌层注册一个媒介，这里一行代码都不用改。
    """

    def create(self, owner_id: str, label: str, ref: str | None = None,
               currency: str = "CNY", medium: str | None = None,
               network: str | None = None, address: str | None = None,
               direction: str = "both", is_default: bool = False) -> dict:
        if not owner_id or not label:
            raise ValueError("owner_id 与 label 必填")
        if direction not in {"pay", "receive", "both"}:
            raise ValueError(f"非法方向：{direction}")
        # 币种 → 媒介：唯一媒介自动锁定；多媒介（同一币种多渠道）必须显式指定，
        # 不指定就替主体挑一个等于替人做资金决策 —— 宁可拒绝，也不猜。
        # exponent 从注册表抄进库里冻结成事实——注册表日后改指数，
        # 这份账户历史金额的解释不能跟着变。
        from a2n_custodian import UnknownMedium, by_currency, get_medium
        if medium:
            med = get_medium(medium)
            if not med:
                raise UnknownMedium(f"未注册的媒介：{medium}")
            if med["currency"].upper() != currency.upper():
                raise ValueError(f"媒介 {medium} 结算 {med['currency']}，与币种 {currency} 不符")
        else:
            candidates = by_currency(currency)
            if not candidates:
                raise UnknownMedium(f"币种 {currency} 没有已注册的媒介（先在持牌层注册）")
            if len(candidates) > 1:
                raise ValueError(
                    f"币种 {currency} 有多个媒介（{sorted(m['code'] for m in candidates)}），"
                    f"请显式指定 medium")
            med = candidates[0]
        aid = f"pa_{new_id('')}"
        ts = now_iso()
        c = conn()
        if is_default:   # 同主体一个币种只有一个默认账户
            c.execute("UPDATE party_accounts SET is_default=0 WHERE owner_id=? AND currency=?",
                      (owner_id, currency))
        c.execute(
            "INSERT INTO party_accounts (account_id, owner_id, label, ref, status, created_at,"
            " medium, currency, exponent, network, address, direction, is_default)"
            " VALUES (?,?,?,?,'ACTIVE',?,?,?,?,?,?,?,?)",
            (aid, owner_id, label, ref, ts, med["code"], currency, int(med["exponent"]),
             network, address, direction, 1 if is_default else 0),
        )
        c.commit()
        publish("account.created", {"account_id": aid, "owner_id": owner_id,
                                    "label": label, "currency": currency,
                                    "medium": med["code"]})
        return self.get(aid)

    def default_of(self, owner_id: str, currency: str) -> dict | None:
        """主体在某币种下的默认结算账户；没有默认就回退该币种第一个 ACTIVE 账户。"""
        r = conn().execute(
            "SELECT * FROM party_accounts WHERE owner_id=? AND currency=? AND status='ACTIVE'"
            " ORDER BY is_default DESC, created_at LIMIT 1", (owner_id, currency),
        ).fetchone()
        return dict(r) if r else None

    def get(self, account_id: str) -> dict | None:
        r = conn().execute("SELECT * FROM party_accounts WHERE account_id=?", (account_id,)).fetchone()
        return dict(r) if r else None

    def list_by_owner(self, owner_id: str) -> list[dict]:
        rows = conn().execute(
            "SELECT * FROM party_accounts WHERE owner_id=? ORDER BY created_at", (owner_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def set_status(self, account_id: str, status: str) -> dict:
        if status not in {"ACTIVE", "FROZEN", "CLOSED"}:
            raise ValueError(f"非法账户状态：{status}")
        if not self.get(account_id):
            raise ValueError("账户不存在")
        conn().execute("UPDATE party_accounts SET status=? WHERE account_id=?", (status, account_id))
        conn().commit()
        publish("account.status", {"account_id": account_id, "status": status})
        return self.get(account_id)


class Peers:
    """对等账户：账户 ↔ agent 的配对关系。

    额度检查需要"该配对下已开未结的金额"，但那是 L3 交易层才知道的事。
    这里用依赖注入，避免 L2 反向依赖 L3。
    """

    def __init__(self) -> None:
        self._exposure = None   # (link_id) -> int（分），未注入时视为 0

    def set_exposure(self, fn) -> None:
        self._exposure = fn

    def _used(self, link_id: str) -> int:
        return int(self._exposure(link_id)) if self._exposure else 0

    def propose(self, account_id: str, agent_id: str, peer_ref: str | None = None,
                terms: dict | None = None, auto_accept: bool = False) -> dict:
        """发起配对。auto_accept 用于 agent 侧已预先同意的场景（演示/自服务）。"""
        if not Accounts().get(account_id):
            raise ValueError("账户不存在")
        a = conn().execute("SELECT agent_id FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        if not a:
            raise ValueError(f"agent 不存在：{agent_id}")
        exists = conn().execute(
            "SELECT link_id, state FROM peer_links WHERE account_id=? AND agent_id=?",
            (account_id, agent_id),
        ).fetchone()
        if exists and exists["state"] != STATE_CLOSED:
            raise ValueError(f"已存在配对 {exists['link_id']}（{exists['state']}），不能重复发起")

        ts = now_iso()
        merged = merge_terms(terms)
        state = STATE_ACTIVE if auto_accept else STATE_PROPOSED
        if exists:
            link_id = exists["link_id"]
            conn().execute(
                "UPDATE peer_links SET peer_ref=?, terms=?, state=?, created_at=?, updated_at=?"
                " WHERE link_id=?", (peer_ref, json.dumps(merged, ensure_ascii=False), state, ts, ts, link_id),
            )
        else:
            link_id = f"pl_{new_id('')}"
            conn().execute(
                "INSERT INTO peer_links (link_id, account_id, agent_id, peer_ref, terms, state,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (link_id, account_id, agent_id, peer_ref,
                 json.dumps(merged, ensure_ascii=False), state, ts, ts),
            )
        conn().commit()
        publish("peer.proposed", {"link_id": link_id, "account_id": account_id, "agent_id": agent_id})
        if state == STATE_ACTIVE:
            publish("peer.activated", {"link_id": link_id, "account_id": account_id, "agent_id": agent_id})
        return self.get(link_id)

    def set_state(self, link_id: str, state: str) -> dict:
        cur = self.get(link_id)
        if not cur:
            raise ValueError("配对不存在")
        if state not in NEXT_STATE.get(cur["state"], set()):
            raise ValueError(f"非法状态迁移：{cur['state']} → {state}")
        conn().execute("UPDATE peer_links SET state=?, updated_at=? WHERE link_id=?",
                       (state, now_iso(), link_id))
        conn().commit()
        if state == STATE_ACTIVE:
            publish("peer.activated", {"link_id": link_id, "account_id": cur["account_id"],
                                       "agent_id": cur["agent_id"]})
        elif state == STATE_CLOSED:
            publish("peer.closed", {"link_id": link_id})
        return self.get(link_id)

    def accept(self, link_id: str) -> dict:
        return self.set_state(link_id, STATE_ACTIVE)

    def suspend(self, link_id: str) -> dict:
        return self.set_state(link_id, STATE_SUSPENDED)

    def close(self, link_id: str) -> dict:
        return self.set_state(link_id, STATE_CLOSED)

    def get(self, link_id: str) -> dict | None:
        r = conn().execute("SELECT * FROM peer_links WHERE link_id=?", (link_id,)).fetchone()
        return self._row(r) if r else None

    def find(self, account_id: str, agent_id: str) -> dict | None:
        r = conn().execute(
            "SELECT * FROM peer_links WHERE account_id=? AND agent_id=?", (account_id, agent_id)
        ).fetchone()
        return self._row(r) if r else None

    def list_by_account(self, account_id: str, only_active: bool = False) -> list[dict]:
        q = "SELECT * FROM peer_links WHERE account_id=?"
        if only_active:
            q += " AND state='ACTIVE'"
        rows = conn().execute(q + " ORDER BY created_at", (account_id,)).fetchall()
        return [self._row(r) for r in rows]

    def list_by_agent(self, agent_id: str, only_active: bool = False) -> list[dict]:
        q = "SELECT * FROM peer_links WHERE agent_id=?"
        if only_active:
            q += " AND state='ACTIVE'"
        rows = conn().execute(q + " ORDER BY created_at", (agent_id,)).fetchall()
        return [self._row(r) for r in rows]

    def usable(self, link_id: str) -> tuple[bool, str]:
        """这笔还能不能做：状态 ACTIVE 且未超额度。"""
        lk = self.get(link_id)
        if not lk:
            return False, "配对不存在"
        if lk["state"] != STATE_ACTIVE:
            return False, f"配对方状态为 {lk['state']}，不能交易"
        limit = int(lk["terms"].get("credit_limit_fen") or 0)
        if limit > 0:
            used = self._used(link_id)
            if used >= limit:
                return False, f"已用 {used} 分，达到额度上限 {limit} 分"
        return True, "ok"

    def _row(self, r) -> dict:
        d = dict(r)
        d["terms"] = json.loads(d["terms"] or "{}")
        return d


accounts = Accounts()
peers = Peers()
