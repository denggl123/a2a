"""M9 行情投影：只读统计。

红线：这里只有统计事实，没有任何报价、推荐、排序建议。
"""
from __future__ import annotations

import json

from a2n_settlement.price import LEGACY_DIM, price_book
from a2n_store import conn

# 任务的两种终态：SETTLED（积分结算完）| ACCEPTED（非积分模式完成）。
# 完成统计一律用这个口径 —— 散落在各处各写一份，迟早有一处漏掉一种。
DONE_STATES = ("SETTLED", "ACCEPTED")

# 金额读方一律 COALESCE(amount_minor, amount)：新数据双写最小单位，
# 老数据只有分。行情算的是同一件事，不许因为老库缺列就少算几笔。
_AMOUNT = "COALESCE(amount_minor, amount, 0)"


def stats() -> dict:
    c = conn()
    agents = c.execute("SELECT COUNT(*) n, SUM(tasks_done) d FROM agents").fetchone()
    tasks = c.execute("SELECT COUNT(*) n FROM tasks").fetchone()
    # 任务终态有两种：SETTLED（积分结算完）与 ACCEPTED（非积分模式——对等账户/
    # 直付/x402 的钱走各自的账，验收通过任务即完成）。以前只数 SETTLED，
    # 主流模式的成功率恒为 0。GMV 仍只计积分任务（amount 是冻结预算，
    # 非积分的真实金额在 deals/pay_charges，不在这里混算）。
    done = c.execute("SELECT COUNT(*) n FROM tasks WHERE state IN ('SETTLED','ACCEPTED')").fetchone()
    settled_points = c.execute(
        "SELECT COALESCE(SUM(amount),0) amt FROM tasks WHERE state='SETTLED'").fetchone()
    rejected = c.execute("SELECT COUNT(*) n FROM tasks WHERE state='REJECTED'").fetchone()
    online = c.execute("SELECT COUNT(*) n FROM agents WHERE last_seen_at IS NOT NULL").fetchone()
    skills = c.execute(
        "SELECT skill_id, COUNT(DISTINCT agent_id) supply FROM skills GROUP BY skill_id ORDER BY supply DESC"
    ).fetchall()
    total = int(tasks["n"] or 0)
    ok = int(done["n"] or 0)
    return {
        "agents_total": int(agents["n"] or 0),
        "agents_online": int(online["n"] or 0),
        "tasks_total": total,
        "tasks_settled": ok,
        "tasks_rejected": int(rejected["n"] or 0),
        "success_rate": round(ok / total, 4) if total else 0.0,
        "gmv_points": int(settled_points["amt"] or 0),
        "skill_supply": [dict(r) for r in skills],
    }


def _listed_prices() -> dict[tuple[str, str], dict]:
    """挂牌价区间：{（能力, 币种）: {lo, hi, n}}。

    读的是**供给方自己**在卡上声明的按次单价（call_count 维度）—— 他们
    愿意接受的价，不是平台给的价。平台在这里只做 min/max 的算术。
    免费能力（卡上没标 call_count）不产生区间：没标价不等于标了 0 元。
    """
    out: dict[tuple[str, str], dict] = {}
    for r in conn().execute("SELECT card_json FROM agents"):
        try:
            card = json.loads(r["card_json"] or "{}")
        except (TypeError, ValueError):
            continue
        for skill, by_cur in price_book(card).items():
            for cur, entries in by_cur.items():
                per_call = next((e for e in entries if e.get("key") == LEGACY_DIM), None)
                if not per_call or int(per_call.get("amount") or 0) <= 0:
                    continue
                a = int(per_call["amount"])
                d = out.setdefault((skill, cur), {"lo": a, "hi": a, "n": 0})
                d["lo"], d["hi"], d["n"] = min(d["lo"], a), max(d["hi"], a), d["n"] + 1
    return out


def board(limit: int = 60) -> dict:
    """行情板：按「能力 × 币种」聚合的**事实**。不定价、不推荐、不排序建议。

    两个口径并排给出，谁也不替代谁：
      挂牌（listed_min/max/count）= 供给方在卡上声明的价 —— **意愿**
      成交（avg/min/max）·   = 验收通过后实际记下的金额 —— **事实**

    多币种**不许混合求平均**：同一能力的 CNY 均价与 USDC 均价是两个
    量纲（分 vs 10⁻⁶），混着算出来的数没有任何解释力。所以按
    （能力, 币种）分行给出，语言交给前端。

    上板条件只有一条：**有人提供**。只挂牌未成交要上，免费（压根没标价）
    也要上 —— 按"有没有价目"建行，免费供给会整类消失，而它恰恰是最该被
    看见的那一类（零成本可调）。行情是供给的地图，不是成交记录的副产品。
    """
    c = conn()
    supply: dict[str, dict] = {}
    for r in c.execute(
        "SELECT s.skill_id, COUNT(DISTINCT s.agent_id) supply,"
        " COUNT(DISTINCT CASE WHEN a.last_seen_at IS NOT NULL THEN s.agent_id END) online"
        " FROM skills s LEFT JOIN agents a ON a.agent_id = s.agent_id"
        " GROUP BY s.skill_id"
    ):
        supply[r["skill_id"]] = {"supply": int(r["supply"] or 0), "online": int(r["online"] or 0)}
    # 发起数：按能力数**全部**任务（含未派单/被驳回）—— 成功率的分母是"点了多少次单"
    requested = {r["skill_id"]: int(r["n"] or 0) for r in c.execute(
        "SELECT skill_id, COUNT(*) n FROM tasks GROUP BY skill_id")}
    # 成交：只数派出去且验收通过的
    settled = c.execute(
        f"SELECT skill_id, COALESCE(currency,'CNY') cur, COUNT(*) n,"
        f" COALESCE(SUM({_AMOUNT}),0) total, MIN({_AMOUNT}) lo, MAX({_AMOUNT}) hi"
        f" FROM tasks WHERE state IN {DONE_STATES} AND node_id IS NOT NULL"
        f" GROUP BY skill_id, cur").fetchall()
    listed = _listed_prices()

    skills: dict[str, dict] = {}

    def slot(sid: str) -> dict:
        # quotes 中间态是 {币种: 条目}：成交与挂牌是两个来源，同一个
        # （能力, 币种）要合并成一行，用 dict 比用 list 找重复可靠。
        return skills.setdefault(sid, {
            "skill_id": sid,
            "supply": supply.get(sid, {}).get("supply", 0),
            "online": supply.get(sid, {}).get("online", 0),
            "requested": requested.get(sid, 0), "done": 0, "quotes": {},
        })

    for sid in supply:
        slot(sid)                                  # 有供给就上板
    for r in settled:
        n = int(r["n"] or 0)
        s = slot(r["skill_id"])
        s["done"] += n
        s["quotes"][r["cur"]] = {
            "currency": r["cur"], "done_count": n,
            "avg_minor": round(float(r["total"] or 0) / n) if n else 0,
            "min_minor": int(r["lo"] or 0), "max_minor": int(r["hi"] or 0),
        }
    for (sid, cur), lk in listed.items():
        q = slot(sid)["quotes"].setdefault(cur, {
            "currency": cur, "done_count": 0, "avg_minor": 0,
            "min_minor": 0, "max_minor": 0})
        q["listed_min_minor"] = lk["lo"]
        q["listed_max_minor"] = lk["hi"]
        q["listed_count"] = lk["n"]

    out = []
    for s in skills.values():
        quotes = []
        for q in s["quotes"].values():
            q.setdefault("listed_min_minor", None)
            q.setdefault("listed_max_minor", None)
            q.setdefault("listed_count", 0)
            quotes.append(q)
        quotes.sort(key=lambda q: (-q["done_count"], q["currency"]))
        s["quotes"] = quotes
        s["success_rate"] = round(s["done"] / s["requested"], 4) if s["requested"] else 0.0
        out.append(s)
    out.sort(key=lambda s: (-s["done"], -s["supply"], s["skill_id"]))
    return {"skills": out[:limit]}
