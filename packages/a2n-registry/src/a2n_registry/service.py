"""M1 身份与注册：Agent Card 的索引与背书。

原则：节点自证能力（card 自持自签），注册表只做索引、验证与背书签名。
注册表不是权威，节点才是。
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from a2n_store import conn
from a2n_kernel.events import publish
from a2n_kernel.hashing import canonical_json, new_id, now_iso, sha256
from a2n_registry.reachability import nat_verdict, normalize_connection, reachable

PROBATION_PROMOTE_TASKS = 3  # 试单期转正所需完成任务数


def card_hash(card: dict) -> str:
    return sha256(canonical_json(card))


def _row_to_agent(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    for k in ("compute", "sla", "price_hint", "metering", "connection"):
        if d.get(k):
            d[k] = json.loads(d[k])
    return d


class Registry:
    def register(self, principal_id: str, card: dict, visibility: str = "public") -> dict:
        for field_name in ("name", "url", "skills"):
            if field_name not in card:
                raise ValueError(f"Agent Card 缺少必填字段: {field_name}")
        ext = card.setdefault("x-a2n", {})
        # 网络唯一标识（UUID）：供给方生成，平台兜底；同 uid 二次注册直接拒绝。
        # 兜底写入 card 后再算 hash——card_hash 覆盖的是"最终背书的这张卡"。
        uid = ext.get("uid") or str(uuid.uuid4())
        ext["uid"] = uid
        if conn().execute("SELECT 1 FROM agents WHERE uid=?", (uid,)).fetchone():
            raise ValueError(f"uid 已被其他 agent 占用：{uid}")
        ch = card_hash(card)
        agent_id = ext.get("node_id") or new_id("ag")
        connection = normalize_connection(ext.get("connection"), card.get("url"))
        c = conn()
        c.execute(
            "INSERT INTO agents (agent_id, principal_id, type, status, kya_grade, visibility,"
            " card_url, card_hash, card_json, name, compute, sla, price_hint, metering,"
            " reputation, tasks_done, earned, registered_at, connection, uid)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                agent_id, principal_id, "node", "PENDING", "C", visibility,
                card.get("url"), ch, json.dumps(card, ensure_ascii=False), card.get("name"),
                json.dumps(ext.get("compute", {}), ensure_ascii=False),
                json.dumps(ext.get("sla", {}), ensure_ascii=False),
                json.dumps(ext.get("price_hint", {}), ensure_ascii=False),
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
        c.commit()
        publish("node.registered", {"agent_id": agent_id, "card_hash": ch, "principal_id": principal_id})
        return self.verify(agent_id)

    def verify(self, agent_id: str) -> dict:
        """验证签名与域名（Mock 实现），通过则进入试单期。"""
        c = conn()
        c.execute("UPDATE agents SET status='PROBATION', kya_grade='B' WHERE agent_id=?", (agent_id,))
        c.commit()
        publish("node.verified", {"agent_id": agent_id})
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
            raise ValueError(f"agent 不存在：{agent_id}")
        ext = card.get("x-a2n", {})
        # uid 是网络唯一身份：已背书的 uid 不可改；旧节点未补录时允许首次写入。
        old_uid = row["uid"]
        new_uid = ext.get("uid")
        if old_uid and new_uid and new_uid != old_uid:
            raise ValueError("uid 是全网唯一身份，不可修改")
        if new_uid and not old_uid:
            if conn().execute("SELECT 1 FROM agents WHERE uid=? AND agent_id<>?", (new_uid, agent_id)).fetchone():
                raise ValueError(f"uid 已被其他 agent 占用：{new_uid}")
        ch = card_hash(card)
        c = conn()
        c.execute(
            "UPDATE agents SET card_hash=?, card_json=?, name=?, compute=?, sla=?,"
            " price_hint=?, metering=?, card_url=?, uid=COALESCE(uid,?) WHERE agent_id=?",
            (ch, json.dumps(card, ensure_ascii=False), card.get("name"),
             json.dumps(ext.get("compute", {}), ensure_ascii=False),
             json.dumps(ext.get("sla", {}), ensure_ascii=False),
             json.dumps(ext.get("price_hint", {}), ensure_ascii=False),
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
        c.commit()
        publish("node.card_updated", {"agent_id": agent_id, "card_hash": ch})
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
            merged.update({k: v for k, v in reported.items() if k != "inbound_ok_at"})
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

    def set_online(self, agent_id: str, online: bool) -> None:
        """在线状态由 Redis 承载；这里用 last_seen_at 近似（本地演示用）。"""
        if not online:
            publish("node.suspended", {"agent_id": agent_id})

    def promote_if_ready(self, agent_id: str) -> None:
        r = conn().execute("SELECT status, tasks_done FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        if r and r["status"] == "PROBATION" and r["tasks_done"] >= PROBATION_PROMOTE_TASKS:
            conn().execute("UPDATE agents SET status='ACTIVE', kya_grade='A' WHERE agent_id=?", (agent_id,))
            conn().commit()
            publish("node.verified", {"agent_id": agent_id, "promoted": True})

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
