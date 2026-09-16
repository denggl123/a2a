"""上架名额：供给方的"允许被发现的数量"，落到"同时能被几个使用者发现"。

它约束的是**分发**，不是能力 —— 这条区分是刻意的：
  - 它是供给方自己的意愿（"我只想同时服务 3 个人"），由平台执行、如实展示；
  - "我能扛多少并发"是自报、平台验不了，早被排除在网络事实之外
    （见 service.deployment_of 的注释：能力是黑盒）。
所以它不是并发额度，而是**发现名额**：把同时使用的人数间接收在 N 以内。

也不进 card：卡是供给方自签的，平台改写卡会让签名当场失效（签名域=整张卡）。
卡里若自己声明了 x-a2n.discover_limit，注册时采纳为初值（那一份是供给方自己签的）。

三条语义：
  ① 按使用者（principal）计名额，一人一个；重复调用只续期，不叠加；
  ② 占用发生在"建立调用关系"那一刻（建任务），不在"被浏览到"那一刻 ——
     看客不该耗光名额，这也是"**间接**控制并发"的意思；
  ③ 闲置 TTL 到期即释放：约束的是"同时"，不是"累计"。一个名额可以被下一个人
     接着用，而不是先到先得永久占有。

满员怎么表现：
  - 对**未持有者**在发现层不可见（不再发给新使用者）；
  - 对**已持有者**照常可见 —— 正在用的东西不能凭空消失；
  - 派单时刻再硬校一次（两个人同时看到、同时下单，只有一个抢得到）。
不做"悄悄降权"这种中间态：要么看得见（且可调用），要么按上面三条解释得清。
"""
from __future__ import annotations

import os
import time

from a2n_kernel.errors import NotFoundError
from a2n_kernel.hashing import now_iso
from a2n_store import conn, tx

DEFAULT_TTL_SECONDS = 1800


def ttl_seconds() -> int:
    """闲置多久释放一个名额。每次读（测试与部署都能即时改），非法值退回默认。"""
    try:
        v = int(os.environ.get("A2N_SEAT_TTL_SECONDS") or DEFAULT_TTL_SECONDS)
    except (TypeError, ValueError):
        return DEFAULT_TTL_SECONDS
    return v if v > 0 else DEFAULT_TTL_SECONDS


def _expiry(ttl: int) -> str:
    """与 now_iso() 同格式（UTC），可以直接做字符串比较，不引入第二套时钟。"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + ttl))


def _limit(c, agent_id: str) -> int:
    r = c.execute("SELECT discover_limit FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
    if not r:
        return 0
    v = r["discover_limit"]
    # 0 / NULL / 脏值一律按"不限"处理：限额是加法，缺省不能变成"谁也发现不到"。
    return int(v) if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0


def limit_of(agent_id: str) -> int:
    return _limit(conn(), agent_id)


def set_limit(agent_id: str, limit: int) -> dict:
    """改上架名额（调用方负责校验归属：只有 owner 能改自己的）。"""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError(f"允许被发现的数量必须是非负整数（0=不限）：{limit!r}")
    with tx() as c:
        if not c.execute("SELECT 1 FROM agents WHERE agent_id=?", (agent_id,)).fetchone():
            raise NotFoundError(f"agent 不存在：{agent_id}")
        c.execute("UPDATE agents SET discover_limit=? WHERE agent_id=?", (limit, agent_id))
    return usage(agent_id)


def _sweep(c, now: str) -> int:
    """回收闲置名额。只在写路径调用（读路径不该写库）。"""
    cur = c.execute("DELETE FROM discovery_seats WHERE expires_at<=?", (now,))
    return cur.rowcount or 0


def sweep(now: str | None = None) -> int:
    with tx() as c:
        return _sweep(c, now or now_iso())


def _live_rows(c, agent_id: str, now: str) -> list:
    return c.execute(
        "SELECT principal_id, task_id, first_seen_at, last_seen_at, expires_at"
        " FROM discovery_seats WHERE agent_id=? AND expires_at>?"
        " ORDER BY last_seen_at", (agent_id, now)).fetchall()


def usage(agent_id: str, viewer: str | None = None, with_holders: bool = False,
          now: str | None = None) -> dict:
    """这个 agent 的名额现状。

    viewer      调用方主体：用来判"这个名额算不算我的"（mine）。
                匿名浏览者（None）一律算"不是我的" —— 新访客就是新使用者。
    with_holders 是否随行带出占用者名单。**只给 owner**：名单是使用者的身份，
                对全网公开等于把"谁在用谁"摊开给别人看。
    """
    now = now or now_iso()
    c = conn()
    limit = _limit(c, agent_id)
    rows = _live_rows(c, agent_id, now) if limit or with_holders else []
    used = len(rows)
    out = {
        "limit": limit, "used": used, "unlimited": limit <= 0,
        "left": None if limit <= 0 else max(0, limit - used),
        "full": limit > 0 and used >= limit,
        "mine": bool(viewer) and any(r["principal_id"] == viewer for r in rows),
        "ttl_seconds": ttl_seconds(),
        "release": "idle",          # 名额只按闲置释放：约束"同时"，不约束"累计"
    }
    if with_holders:
        out["holders"] = [
            {"principal_id": r["principal_id"], "task_id": r["task_id"],
             "last_seen_at": r["last_seen_at"], "expires_at": r["expires_at"]}
            for r in rows]
    return out


def expose(agent: dict, viewer: str | None = None, include_unlisted: bool = False,
           now: str | None = None) -> tuple[bool, dict]:
    """这个 agent 该不该出现在 viewer 的发现结果里 —— 发现的**唯一**可见性出口。

    两条判据合起来（都是供给方的分发策略，不是能力，也不涉钱）：
      ① visibility：public 人人可见；unlisted / private 只进 include_unlisted 的
         内部派单视图 —— 它们不是"不能调"，是"不进公开名单"（点名/收藏照样调）；
      ② 名额：满员且名额不是这个人的 → 不发给新使用者。
    发现层与控制台列表都问这一处，别再各写一个"够不够"的判断：
    两个接口各写一遍，迟早对"能不能被发现"给出两种答案。

    返回 (可见, {reason, seats})。reason 是给"看不见"的动作留的可解释出口：
    凭空消失比看得见但不能调更糟。
    """
    vis = (agent or {}).get("visibility") or "public"
    if not include_unlisted and vis != "public":
        return False, {"reason": f"{vis}：不在公开名单里（供给方只让它被点名/收藏的调用方看到）",
                       "seats": None}
    aid = (agent or {}).get("agent_id")
    lim = (agent or {}).get("discover_limit")
    if not (isinstance(lim, int) and not isinstance(lim, bool) and lim > 0):
        # 不限名额（0/缺省）：**不查库**（发现列表里绝大多数 agent 都是这种，
        # 别为"没设限额"给每个候选加两次查询），但仍返回同样形状的"不限"形态 ——
        # 同一件事在列表接口与发现接口里不能长两张脸（一张 None、一张 0/0）。
        return True, {"reason": "ok", "seats": {
            "limit": 0, "used": 0, "unlimited": True, "left": None, "full": False,
            "mine": False, "ttl_seconds": ttl_seconds(), "release": "idle"}}
    u = usage(aid, viewer=viewer, now=now) if aid else None
    if u and u["full"] and not u["mine"]:
        return False, {"reason": f"名额已满（{u['used']}/{u['limit']}）："
                                 f"新使用者暂时发现不到它，闲置会释放名额",
                       "seats": u}
    return True, {"reason": "ok", "seats": u}


def take(agent_id: str, principal_id: str, task_id: str | None = None,
         commit: bool = True, now: str | None = None) -> tuple[bool, dict]:
    """占一个名额（幂等 + 续期）。返回 (是否拿到, 说明)。

    commit=False 用于并入外层事务（建任务的同一笔写）：名额与任务同生共死，
    不留"任务没建成、名额却占了"的窗口。
    """
    now = now or now_iso()
    if commit:
        with tx() as c:
            return _take(c, agent_id, principal_id, task_id, now)
    return _take(conn(), agent_id, principal_id, task_id, now)


def _take(c, agent_id: str, principal_id: str, task_id: str | None, now: str) -> tuple[bool, dict]:
    _sweep(c, now)
    limit = _limit(c, agent_id)
    rows = _live_rows(c, agent_id, now)
    used = len(rows)
    ttl = ttl_seconds()
    base = {"limit": limit, "unlimited": limit <= 0, "ttl_seconds": ttl}

    if any(r["principal_id"] == principal_id for r in rows):
        c.execute(
            "UPDATE discovery_seats SET last_seen_at=?, expires_at=?,"
            " task_id=COALESCE(?, task_id) WHERE agent_id=? AND principal_id=?",
            (now, _expiry(ttl), task_id, agent_id, principal_id))
        return True, {**base, "used": used, "left": None if limit <= 0 else max(0, limit - used),
                      "renewed": True, "reason": "名额续期（你已经在用这个 agent）"}

    if limit > 0 and used >= limit:
        return False, {**base, "used": used, "left": 0, "renewed": False,
                       "reason": (f"名额已满（{used}/{limit}）：它只允许被 {limit} 个使用者发现，"
                                  f"闲置 {max(1, ttl // 60)} 分钟会释放一个名额")}

    c.execute(
        "INSERT INTO discovery_seats (agent_id, principal_id, task_id, first_seen_at,"
        " last_seen_at, expires_at) VALUES (?,?,?,?,?,?)",
        (agent_id, principal_id, task_id, now, now, _expiry(ttl)))
    return True, {**base, "used": used + 1,
                  "left": None if limit <= 0 else max(0, limit - used - 1),
                  "renewed": False, "reason": "已占一个名额"}
