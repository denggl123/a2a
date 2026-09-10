"""M1 身份与注册：Agent Card 的索引与背书。

原则：节点自证能力（card 自持自签），注册表只做索引、验证与背书签名。
注册表不是权威，节点才是。
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from a2n_store import conn, tx
from a2n_kernel.errors import ConflictError, NotFoundError, ValidationError
from a2n_kernel.events import publish
from a2n_kernel.hashing import canonical_json, new_id, now_iso, sha256
from a2n_registry.reachability import nat_verdict, normalize_connection, reachable

PROBATION_PROMOTE_TASKS = 3  # 试单期转正所需完成任务数
OBS_WINDOW = 20              # 使用端实测滑动窗口（与 SDK 自报窗口同宽）


def card_hash(card: dict) -> str:
    return sha256(canonical_json(card))


def _row_to_agent(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    for k in ("compute", "sla", "price_hint", "metering", "connection"):
        if d.get(k):
            d[k] = json.loads(d[k])
    return d


def validate_card(card: dict) -> None:
    """卡结构校验：不管从管理台、SDK 还是协议入口上架，卡都是同一种形状。

    只拒绝"形状性违规"（缺字段/格式错/明显不合逻辑），不做业务判断——
    技能是否有人要、价格是否合理，是市场的视线，不是注册表的。
    """
    if not isinstance(card, dict):
        raise ValidationError("Agent Card 必须是 JSON 对象")
    for field_name in ("name", "url", "skills"):
        if field_name not in card:
            raise ValidationError(f"Agent Card 缺少必填字段: {field_name}")
    if not str(card.get("url") or "").startswith(("http://", "https://")):
        raise ValidationError(f"url 必须是 http(s) 地址：{card.get('url')!r}")
    skills = card.get("skills")
    if not isinstance(skills, list) or not skills:
        raise ValidationError("skills 必须是非空数组")
    for s in skills:
        if not isinstance(s, dict) or not str(s.get("id") or "").strip():
            raise ValidationError(f"每条技能都要有非空 id：{s!r}")
    ext = card.get("x-a2n") or {}
    if ext.get("uid"):
        try:
            uuid.UUID(str(ext["uid"]))
        except (ValueError, AttributeError, TypeError):
            raise ValidationError(f"uid 必须是 UUID 格式：{ext['uid']!r}")
    book = ext.get("price_book") or card.get("price_book")
    if book:
        if not isinstance(book, dict):
            raise ValidationError("price_book 必须是 {技能: {币种: 条目}} 结构")
        for skill, by_cur in book.items():
            if not isinstance(by_cur, dict):
                raise ValidationError(f"price_book[{skill!r}] 必须是 {{币种: 条目}}")
            for cur, spec in by_cur.items():
                dims = (spec or {}).get("dimensions") if isinstance(spec, dict) else spec
                if not isinstance(dims, list) or not dims:
                    raise ValidationError(f"price_book[{skill!r}][{cur}] 缺 dimensions")
                for e in dims:
                    if not isinstance(e, dict) or not e.get("key"):
                        raise ValidationError(f"价目条目缺 key：{e!r}")
                    amount = e.get("amount")
                    if not isinstance(amount, int) or amount <= 0:
                        raise ValidationError(f"价目条目 amount 必须为正整数：{e!r}")
                    per = e.get("per", 1)
                    if not isinstance(per, int) or per <= 0:
                        raise ValidationError(f"价目条目 per 必须为正整数：{e!r}")
    metering = (ext.get("metering") or {}).get("dimensions")
    if metering:
        for d in metering:
            if not isinstance(d, dict) or not str(d.get("key") or "").strip():
                raise ValidationError(f"计量维度缺 key：{d!r}")


class Registry:
    def register(self, principal_id: str, card: dict, visibility: str = "public") -> dict:
        validate_card(card)
        ext = card.setdefault("x-a2n", {})
        # 网络唯一标识（UUID）：供给方生成，平台兜底；同 uid 二次注册直接拒绝。
        # 兜底写入 card 后再算 hash——card_hash 覆盖的是"最终背书的这张卡"。
        uid = ext.get("uid") or str(uuid.uuid4())
        ext["uid"] = uid
        if conn().execute("SELECT 1 FROM agents WHERE uid=?", (uid,)).fetchone():
            raise ConflictError(f"uid 已被其他 agent 占用：{uid}")
        ch = card_hash(card)
        agent_id = ext.get("node_id") or new_id("ag")
        connection = normalize_connection(ext.get("connection"), card.get("url"))
        c = conn()
        c.execute(
            "INSERT INTO agents (agent_id, principal_id, type, status, kya_grade, visibility,"
            " card_url, card_hash, card_json, name, compute, sla, metering,"
            " reputation, tasks_done, earned, registered_at, connection, uid)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                agent_id, principal_id, "node", "PENDING", "C", visibility,
                card.get("url"), ch, json.dumps(card, ensure_ascii=False), card.get("name"),
                json.dumps(ext.get("compute", {}), ensure_ascii=False),
                json.dumps(ext.get("sla", {}), ensure_ascii=False),
                json.dumps(ext.get("metering", {}), ensure_ascii=False),
                0.5, 0, 0, now_iso(), json.dumps(connection, ensure_ascii=False),
                uid,
            ),
        )
        for s in card.get("skills", []):
            c.execute(
                "INSERT INTO skills (agent_id, skill_id, skill_version, tags, input_modes) VALUES (?,?,?,?,?)",
                (agent_id, s.get("id"), s.get("version", "1.0.0"),
                 json.dumps(s.get("tags", []), ensure_ascii=False),
                 json.dumps(s.get("inputModes", []), ensure_ascii=False)),
            )
        publish("node.registered", {"agent_id": agent_id, "card_hash": ch, "principal_id": principal_id})
        c.commit()
        return self.verify(agent_id)

    def verify(self, agent_id: str) -> dict:
        """验证签名与域名（Mock 实现），通过则进入试单期。"""
        c = conn()
        c.execute("UPDATE agents SET status='PROBATION', kya_grade='B' WHERE agent_id=?", (agent_id,))
        publish("node.verified", {"agent_id": agent_id})
        c.commit()
        return self.get(agent_id)

    def update_card(self, agent_id: str, card: dict) -> dict:
        """供给方整卡更新（改价、改算力、改结算方式）。

        改价是供给侧的正当行为——快照机制保证已成交的合约不受影响
        （tasks.unit_prices / pay_charges 快照在成交时点已冻结）；这里
        只负责让"下一单"看到新行情：card_hash 同步重算，派单校验
        （assignable 的 hash 比对）从此认新版，杜绝能力被偷偷改弱。
        只允许主体自己更新，调用方（路由层）负责校验归属。
        """
        for field_name in ("name", "url", "skills"):
            if field_name not in card:
                raise ValueError(f"Agent Card 缺少必填字段: {field_name}")
        row = conn().execute("SELECT agent_id, uid FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        if not row:
            raise NotFoundError(f"agent 不存在：{agent_id}")
        ext = card.get("x-a2n", {})
        # uid 是网络唯一身份：已背书的 uid 不可改；旧节点未补录时允许首次写入。
        old_uid = row["uid"]
        new_uid = ext.get("uid")
        if old_uid and new_uid and new_uid != old_uid:
            raise ConflictError("uid 是全网唯一身份，不可修改")
        if new_uid and not old_uid:
            if conn().execute("SELECT 1 FROM agents WHERE uid=? AND agent_id<>?", (new_uid, agent_id)).fetchone():
                raise ConflictError(f"uid 已被其他 agent 占用：{new_uid}")
        ch = card_hash(card)
        c = conn()
        c.execute(
            # price_hint 列不再写：价目事实只在 card_json 里（v1/v2 由
            # settlement.price_book 统一解释），冗余列只会制造第二个真相。
            "UPDATE agents SET card_hash=?, card_json=?, name=?, compute=?, sla=?,"
            " metering=?, card_url=?, uid=COALESCE(uid,?) WHERE agent_id=?",
            (ch, json.dumps(card, ensure_ascii=False), card.get("name"),
             json.dumps(ext.get("compute", {}), ensure_ascii=False),
             json.dumps(ext.get("sla", {}), ensure_ascii=False),
             json.dumps(ext.get("metering", {}), ensure_ascii=False),
             card.get("url"), new_uid, agent_id),
        )
        c.execute("DELETE FROM skills WHERE agent_id=?", (agent_id,))
        for s in card.get("skills", []):
            c.execute(
                "INSERT INTO skills (agent_id, skill_id, skill_version, tags, input_modes) VALUES (?,?,?,?,?)",
                (agent_id, s.get("id"), s.get("version", "1.0.0"),
                 json.dumps(s.get("tags", []), ensure_ascii=False),
                 json.dumps(s.get("inputModes", []), ensure_ascii=False)),
            )
        publish("node.card_updated", {"agent_id": agent_id, "card_hash": ch})
        c.commit()
        return self.get(agent_id)

    def get(self, agent_id: str) -> dict | None:
        r = conn().execute("SELECT * FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        return _row_to_agent(r) if r else None

    def heartbeat(self, agent_id: str, connection: dict | None = None,
                  peer_ip: str | None = None) -> dict:
        """心跳 = 保活 + 连接状态上报。

        pull/wss 模式下，心跳本身就是"我在线且能出网"的证据，不需要任何入站通道。
        平台顺手把自己的观测（出口 IP、NAT 判定）回给节点，让节点自己看得见处境。
        """
        c = conn()
        row = c.execute("SELECT * FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        if not row:
            return {"agent_id": agent_id, "error": "agent 不存在"}

        merged = json.loads(row["connection"]) if row["connection"] else {}
        if connection:
            reported = normalize_connection(connection, row["card_url"])
            # rtt_ms 是平台探测值，心跳覆盖会把上次探测结果冲成 None——保留旧值
            merged.update({k: v for k, v in reported.items()
                           if k not in ("inbound_ok_at", "rtt_ms")})
            # 服务质量自报（TTFT 平均等）：节点自报 = 内部真相，随连接状态一起存
            if connection.get("metrics"):
                merged["metrics"] = connection["metrics"]
        local_ips = merged.get("local_ips") or []
        merged["nat"] = nat_verdict(peer_ip, local_ips)

        c.execute("UPDATE agents SET last_seen_at=?, connection=?, peer_ip=COALESCE(?, peer_ip)"
                  " WHERE agent_id=?",
                  (now_iso(), json.dumps(merged, ensure_ascii=False), peer_ip, agent_id))
        c.commit()

        agent = self.get(agent_id) or {}
        ok, why = reachable(agent)
        return {
            "agent_id": agent_id,
            "last_seen_at": now_iso(),
            "peer_ip": peer_ip or row["peer_ip"],
            "nat": merged["nat"],
            "mode": merged.get("mode", "pull"),
            "reachable": ok,
            "reason": why,
            "downgraded": merged.get("downgraded"),
        }

    def observe(self, agent_id: str, requester_id: str, obs: dict) -> dict:
        """使用端实测回传：发现页里唯一"两个 SDK 之间"的延时真相。

        数据归属铁律：端到端往返只有调用方测得到（P2P 下平台测不了），
        所以这条数据只能由使用端产生、回传给平台聚合。
        防刷：绑 task_id 时严格校验任务存在且 requester/node 两端对得上；
        无任务绑定（relay 直调没有任务）只进窗口，一期接受，生产应强制绑定。
        表归属：agents 归 registry，使用端观测也由这里落库（connection.observed），
        读-改-写包 store.tx()，与心跳/探测并发不丢数据。
        """
        task_id = (obs or {}).get("task_id")
        if task_id:
            t = conn().execute("SELECT requester_id, node_id FROM tasks WHERE id=?",
                               (task_id,)).fetchone()
            if not t or t["requester_id"] != requester_id or t["node_id"] != agent_id:
                raise ConflictError("观测与任务对不上：task_id 不存在或两端不符")

        def _merge(c: sqlite3.Row) -> str:
            data = json.loads(c["connection"]) if c["connection"] else {}
            ob = data.get("observed") or {"rtt": [], "total": []}
            for key, val in (("rtt", (obs or {}).get("rtt_ms")),
                             ("total", (obs or {}).get("total_ms"))):
                if isinstance(val, int) and 0 <= val < 10 ** 7:
                    ob[key] = (ob.get(key) or [])[ -OBS_WINDOW + 1:] + [val]
            ob["samples"] = max(len(ob.get("rtt") or []), len(ob.get("total") or []))
            ob["at"] = now_iso()
            data["observed"] = ob
            return json.dumps(data, ensure_ascii=False)

        with tx() as c:
            row = c.execute("SELECT connection FROM agents WHERE agent_id=?",
                            (agent_id,)).fetchone()
            if not row:
                raise NotFoundError("agent 不存在")
            c.execute("UPDATE agents SET connection=? WHERE agent_id=?",
                      (_merge(row), agent_id))
        raw = conn().execute("SELECT connection FROM agents WHERE agent_id=?",
                             (agent_id,)).fetchone()["connection"]
        ob = json.loads(raw)["observed"]
        return {"agent_id": agent_id, "observed": {
            "rtt_avg_ms": int(sum(ob["rtt"]) / len(ob["rtt"])) if ob.get("rtt") else None,
            "total_avg_ms": int(sum(ob["total"]) / len(ob["total"])) if ob.get("total") else None,
            "samples": ob["samples"]}}

    def set_online(self, agent_id: str, online: bool) -> None:
        """在线状态由 Redis 承载；这里用 last_seen_at 近似（本地演示用）。"""
        if not online:
            publish("node.suspended", {"agent_id": agent_id})

    def promote_if_ready(self, agent_id: str) -> None:
        r = conn().execute("SELECT status, tasks_done FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        if r and r["status"] == "PROBATION" and r["tasks_done"] >= PROBATION_PROMOTE_TASKS:
            conn().execute("UPDATE agents SET status='ACTIVE', kya_grade='A' WHERE agent_id=?", (agent_id,))
            publish("node.verified", {"agent_id": agent_id, "promoted": True})
            conn().commit()

    def bump_reputation(self, agent_id: str, delta: float) -> float:
        """信誉落库：agents 表归 registry 管，别的域只能经这里改。

        "事件怎么折算成分数"是信誉域的事（模型在 a2n-reputation），
        "分数怎么落库、怎么不被并发写丢"是这里的事。用显式事务串起来 ——
        以前这一行 UPDATE 散在信誉包里，两个事件并发到达会静默丢一次。
        """
        with tx():
            r = conn().execute(
                "SELECT reputation FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
            if not r:
                return 0.5
            cur = float(r["reputation"] or 0.5)
            new_score = max(0.0, min(1.0, round(cur + float(delta), 4)))
            conn().execute("UPDATE agents SET reputation=? WHERE agent_id=?",
                           (new_score, agent_id))
        return new_score

    def list_by_principal(self, principal_id: str) -> list[dict]:
        rows = conn().execute(
            "SELECT * FROM agents WHERE principal_id=? ORDER BY registered_at DESC", (principal_id,)
        ).fetchall()
        return [_row_to_agent(r) for r in rows]

    def list_all(self) -> list[dict]:
        rows = conn().execute("SELECT * FROM agents ORDER BY registered_at DESC").fetchall()
        return [_row_to_agent(r) for r in rows]

    def credit(self, agent_id: str, amount: int) -> None:
        conn().execute(
            "UPDATE agents SET tasks_done = tasks_done + 1, earned = earned + ? WHERE agent_id=?",
            (amount, agent_id),
        )
        conn().commit()
        self.promote_if_ready(agent_id)


registry = Registry()
