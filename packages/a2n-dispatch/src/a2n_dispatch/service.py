"""M4 调度与发现：三级漏斗 —— 能力匹配 → 属性过滤 → 信誉排序。

红线：price_hint 不参与排序。一旦按提示价排序，注册表就变成了报价排序器，
那就等于平台在定价，铁律一当场破功。
"""
from __future__ import annotations

import json
import re
from typing import Any

from a2n_store import conn
from a2n_registry.reachability import reachable

# 派单前的轻量校验：发现自由，派单受控
ASSIGNABLE_STATUS = {"ACTIVE", "PROBATION"}

# 这些模式存在"能从外部打进来"的入口，对外投影统一换成平台中继地址
RELAYABLE_MODES = {"tunnel", "relay", "direct", "holepunch"}


def _ver_ok(required: str | None, actual: str) -> bool:
    if not required:
        return True
    m = re.match(r">=\s*([\d.]+)", required)
    if m:
        return _vtuple(actual) >= _vtuple(m.group(1))
    return actual == required


def _vtuple(v: str) -> tuple:
    return tuple(int(x) for x in str(v).split("."))


class Discovery:
    def query(self, require: dict, filt: dict | None = None, sort: list | None = None,
              limit: int = 20, include_unlisted: bool = False) -> list[dict[str, Any]]:
        filt = filt or {}
        skill = require.get("skill")
        c = conn()

        # ① 能力匹配（硬条件）
        rows = c.execute(
            "SELECT DISTINCT agent_id FROM skills WHERE skill_id=?", (skill,)
        ).fetchall() if skill else c.execute("SELECT DISTINCT agent_id FROM skills").fetchall()
        agent_ids = [r["agent_id"] for r in rows]

        # ② 属性过滤（硬条件）
        out = []
        for aid in agent_ids:
            a = c.execute("SELECT * FROM agents WHERE agent_id=?", (aid,)).fetchone()
            if not a:
                continue
            if not include_unlisted and a["visibility"] != "public":
                continue
            if a["status"] in {"SUSPENDED", "DELISTED", "BLACKLISTED"}:
                continue
            if "status" in filt and a["status"] not in filt["status"]:
                continue
            if "kya_grade" in filt and a["kya_grade"] not in filt["kya_grade"]:
                continue
            compute = json.loads(a["compute"] or "{}")
            if "region" in filt and compute.get("region") not in filt["region"]:
                continue
            if "vram_gb" in filt:
                need = filt["vram_gb"].get(">=", 0) if isinstance(filt["vram_gb"], dict) else filt["vram_gb"]
                if (compute.get("vram_gb") or 0) < need:
                    continue
            if "online" in filt and filt["online"] and not a["last_seen_at"]:
                continue
            conn_json = json.loads(a["connection"] or "{}")
            if "connection_mode" in filt and conn_json.get("mode") not in filt["connection_mode"]:
                continue
            # 结算方式：一期只有声明了 peer_account 的 agent 才支持对等账户。
            # 没声明 = 只接受预付费，一期就拿它做不了交易。
            card = json.loads(a["card_json"] or "{}")
            accepts = card.get("accepts") or card.get("x-a2n", {}).get("accepts") or []
            if isinstance(accepts, str):
                accepts = [accepts]
            if "accepts" in filt and not set(filt["accepts"]) & set(accepts):
                continue
            # ---- 多维筛选：一切以 card 里声明的字段为准 ----
            if "gpu" in filt and compute.get("gpu") != filt["gpu"]:
                continue
            if "concurrency" in filt and (compute.get("concurrency") or 0) < filt["concurrency"]:
                continue
            sla = json.loads(a["sla"] or "{}")
            if "max_latency_ms" in filt and (sla.get("max_latency_ms") or 10 ** 9) > filt["max_latency_ms"]:
                continue
            if "tags" in filt:
                stags: set[str] = set()
                for srow in c.execute("SELECT tags FROM skills WHERE agent_id=?", (aid,)):
                    stags |= set(json.loads(srow["tags"] or "[]"))
                if not set(filt["tags"]) & stags:
                    continue
            if "metering_dim" in filt:
                dims_declared = {d.get("key") for d in
                                 (json.loads(a["metering"] or "{}").get("dimensions") or [])}
                if filt["metering_dim"] not in dims_declared:
                    continue
            if "min_reputation" in filt and (a["reputation"] or 0) < filt["min_reputation"]:
                continue
            # 预算上限是使用方消费行情的方式，不是平台定价；price_hint 仍不参与排序
            if "max_price_hint" in filt and skill:
                hint = (json.loads(a["price_hint"] or "{}").get(skill) or {})
                if hint and float(hint.get("amount", 0)) > float(filt["max_price_hint"]):
                    continue
            # 可达性：pull/wss 只比心跳时间戳（零成本）；direct/relay 走带缓存的入站探测
            ok, why = reachable(dict(a, connection=conn_json))
            if "reachable" in filt and ok != bool(filt["reachable"]):
                continue
            # 连接投影：对外只发 A2N 的中继入口，绝不暴露节点真实地址。
            # direct 节点的原始 url 只留在注册表内部供平台协商使用；
            # peer_ip 是平台侧 NAT 观测，也不对外。防的就是"拿 card 里的
            # 地址绕开 A2N 直连节点"——那等于绕过计量、对账与门禁，越权。
            projected_url = (f"/v1/relay/{a['agent_id']}"
                             if conn_json.get("mode") in RELAYABLE_MODES else None)
            out.append({
                "agent_id": a["agent_id"],
                "name": a["name"],
                "status": a["status"],
                "kya_grade": a["kya_grade"],
                "reputation": a["reputation"],
                "card_hash": a["card_hash"],
                "compute": compute,
                "sla": json.loads(a["sla"] or "{}"),
                "price_hint": json.loads(a["price_hint"] or "{}"),
                "accepts": accepts,
                "tasks_done": a["tasks_done"],
                "earned": a["earned"],
                "connection": {"mode": conn_json.get("mode", "pull"),
                               "url": projected_url,
                               "nat": conn_json.get("nat", "unknown")},
                "reachable": ok,
                "reach_reason": why,
            })

        # ③ 信誉排序（软条件）—— 只用第三方验证过的事实
        sort = sort or [{"reputation": "desc"}]
        for s in reversed(sort):  # 逆序多键排序，最后一个键为主键
            for key, direction in s.items():
                if key == "price_hint":
                    continue  # 红线：提示价不参与排序
                out.sort(key=lambda x: x.get(key) or 0, reverse=(direction == "desc"))
        return out[:limit]

    def assignable(self, agent_id: str, card_hash: str | None = None) -> tuple[bool, str]:
        """派单前轻量校验（涉及钱，必须受控）。

        发现是自由的，派单是受控的：一个 NAT 后面没打通道的节点照样能被搜到，
        但派单这一刻必须拦下 —— 否则使用方的预算会冻在永远不会被执行的任务上。
        """
        a = conn().execute("SELECT * FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        if not a:
            return False, "agent 不存在"
        if a["status"] not in ASSIGNABLE_STATUS:
            return False, f"状态不允许派单：{a['status']}"
        if card_hash and a["card_hash"] != card_hash:
            return False, "card 已变更，请更新后重试（防能力被偷偷改弱）"
        ok, why = reachable(dict(a, connection=json.loads(a["connection"] or "{}")))
        if not ok:
            return False, f"节点不可达：{why}"
        return True, "ok"


discovery = Discovery()
