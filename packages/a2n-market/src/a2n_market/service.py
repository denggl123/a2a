"""M9 行情投影：只读统计。

红线：这里只有统计事实，没有任何报价、推荐、排序建议。
"""
from __future__ import annotations

from a2n_store import conn


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
