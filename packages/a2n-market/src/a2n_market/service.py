"""M9 行情投影：只读统计。

红线：这里只有统计事实，没有任何报价、推荐、排序建议。
"""
from __future__ import annotations

from a2n_store import conn


def stats() -> dict:
    c = conn()
    agents = c.execute("SELECT COUNT(*) n, SUM(tasks_done) d FROM agents").fetchone()
    tasks = c.execute("SELECT COUNT(*) n FROM tasks").fetchone()
    settled = c.execute("SELECT COUNT(*) n, COALESCE(SUM(amount),0) amt FROM tasks WHERE state='SETTLED'").fetchone()
    rejected = c.execute("SELECT COUNT(*) n FROM tasks WHERE state='REJECTED'").fetchone()
    online = c.execute("SELECT COUNT(*) n FROM agents WHERE last_seen_at IS NOT NULL").fetchone()
    skills = c.execute(
        "SELECT skill_id, COUNT(DISTINCT agent_id) supply FROM skills GROUP BY skill_id ORDER BY supply DESC"
    ).fetchall()
    total = int(tasks["n"] or 0)
    ok = int(settled["n"] or 0)
    return {
        "agents_total": int(agents["n"] or 0),
        "agents_online": int(online["n"] or 0),
        "tasks_total": total,
        "tasks_settled": ok,
        "tasks_rejected": int(rejected["n"] or 0),
        "success_rate": round(ok / total, 4) if total else 0.0,
        "gmv_points": int(settled["amt"] or 0),
        "skill_supply": [dict(r) for r in skills],
    }
