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
from a2n_p2p.attest import sovereign_ext, verify_selfproof
from a2n_registry import trial
from a2n_registry.reachability import nat_verdict, normalize_connection, reachable

PROBATION_PROMOTE_TASKS = 3  # 试单期转正所需完成任务数
OBS_WINDOW = 20              # 使用端实测滑动窗口（与 SDK 自报窗口同宽）

# 卡自证的三态（发现路与注册路共用同一处判据）。
# 刻意把"没验"与"验不过"分开：把没声明的卡也说成"验过了"是另一种撒谎。
SELF_SIGNED = "signed"        # 卡自带身份且验签通过 → 算"已自证"
UNATTESTED = "unattested"     # 卡没声明身份 → 能发现，但不等于验过
CARD_INVALID = "invalid"      # 自称了身份却验不过 → 冒名 / 被改 → 排除
CARD_TAMPERED = "tampered"    # 卡哈希与注册时不一致 → 入库后被改 → 排除
# 硬排除的两种（"验不过的卡不许出现在可直接调用里"）
CARD_REJECTED = frozenset({CARD_INVALID, CARD_TAMPERED})


def card_hash(card: dict) -> str:
    return sha256(canonical_json(card))


def verify_card(card: dict, *, require_endpoint: bool = False) -> tuple[bool, str]:
    """验卡三步：①形状 ②身份自洽 ③签名。

    与自持模式（a2n-node.card）**同一份实现**——那个模块只是把这里的函数
    转出去。两条路各写一遍，同一张卡迟早会有两个结论。
    """
    if not isinstance(card, dict):
        return False, "卡必须是 JSON 对象"
    try:
        validate_card(card)
    except Exception as e:  # noqa: BLE001 - 形状错照实说，不吞
        return False, f"卡形状不合规：{e}"
    ok, why = verify_selfproof(card)
    if not ok:
        return False, why
    if require_endpoint and not card.get("url"):
        return False, "卡没有可直连地址（url 为空）：无托管模式没有中继可退"
    return True, "ok"


def card_verdict(card: dict, stored_hash: str | None = None) -> dict:
    """这张卡"能不能当可直接调用的凭据"—— 发现、注册、派单共用的同一处判据。

    返回 {verified, selfproof, reason}。三态诚实区分：
      signed     身份验签通过        → 可直接调用
      unattested 卡没声明身份        → 能发现，但不算"已自证"
      invalid    自称身份却验不过     → 冒名 / 被改 → 排除
    另：stored_hash（注册时入库的卡哈希）与卡重算的哈希不符 → tampered → 排除。
    "在你列表里就等于验过了"这条主张，靠的就是这里不把 unattested 说成 verified。
    """
    if not isinstance(card, dict) or not card:
        return {"verified": False, "selfproof": UNATTESTED, "reason": "卡为空"}
    if stored_hash is not None and card_hash(card) != stored_hash:
        return {"verified": False, "selfproof": CARD_TAMPERED,
                "reason": "卡哈希与注册时不一致（入库后被改过）"}
    sov = sovereign_ext(card)
    if not any(sov.get(k) for k in ("did", "pub", "sig")):
        return {"verified": False, "selfproof": UNATTESTED,
                "reason": "卡未自证身份（x-a2n.sovereign 为空）"}
    ok, why = verify_card(card)
    if not ok:
        return {"verified": False, "selfproof": CARD_INVALID, "reason": why}
    return {"verified": True, "selfproof": SELF_SIGNED,
            "reason": "已自证（身份=公钥指纹，签名有效）"}


def deployment_of(ext: dict) -> dict:
    """部署属地：卡里唯一保留的"运行环境"事实。

    **能力是黑盒**：GPU 型号、显存、CPU 核数、并发数都是实现细节，
    且全部自报、平台无法验证 —— 把它们写进网络事实只会制造不可信字段，
    还把"实现"暴露成"契约"（节点换张卡，契约就变了）。
    只有"部署属地"是使用方真正需要的（数据驻留合规、就近调用），予以保留。

    新卡写在 x-a2n.deployment.region；老卡的 compute.region 继续认（兼容）。
    """
    dep = (ext.get("deployment") or {})
    region = dep.get("region") or (ext.get("compute") or {}).get("region")
    return {"region": region} if region else {}


def _row_to_agent(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    for k in ("compute", "sla", "metering", "connection"):
        if d.get(k):
            d[k] = json.loads(d[k])
    # price_hint 是 v1 遗留列、v2 起不再写入（价目事实只在 card_json 里，由
    # settlement.price_book 统一解释）。留着一个恒为 null 的列会制造"第二个
    # 真相"：调用方看到 price_hint=null 而 card 里明明有价，无从判断该信谁。
    # 所以投影里直接不出现——价目请从 card 派生。
    d.pop("price_hint", None)
    return d


def validate_card(card: dict) -> None:
    """卡结构校验：不管从管理台、SDK 还是协议入口上架，卡都是同一种形状。

    只拒绝"形状性违规"（缺字段/格式错/明显不合逻辑），不做业务判断——
    技能是否有人要、价格是否合理，是市场的视线，不是注册表的。
    """
    if not isinstance(card, dict):
        raise ValidationError("Agent Card 必须是 JSON 对象")
    for field_name in ("name", "skills"):
        if field_name not in card:
            raise ValidationError(f"Agent Card 缺少必填字段: {field_name}")
    # url 可空：relay/pull 模式节点没有自有公网入口（全程出站连接），
    # 对外调用一律走 A2N 门牌号 /v1/relay/{id}（地址投影）。给了值就必须是 http(s)。
    url = card.get("url")
    if url and not str(url).startswith(("http://", "https://")):
        raise ValidationError(f"url 必须是 http(s) 地址或留空（走平台中继门牌号）：{url!r}")
    skills = card.get("skills")
    if not isinstance(skills, list) or not skills:
        raise ValidationError("skills 必须是非空数组")
    for s in skills:
        if not isinstance(s, dict) or not str(s.get("id") or "").strip():
            raise ValidationError(f"每条技能都要有非空 id：{s!r}")
    ext = card.get("x-a2n") or {}
    # 卡上不许声明"我不走免费期"：**任何想被发现的 agent，前 10 次完成调用不计费**
    # （目的在使用方 —— 谁都能先看真实案例再决定付不付钱）。这里**直接拒**而不是
    # 静默忽略：忽略等于让供给方以为自己退出了，等到被计费才发现，那是欺骗。
    # 部署级开关 A2N_TRIAL_DEFAULT 是测试/演示用的，不是供给方的出口。
    if ext.get("trial") is False:
        raise ValidationError(
            "不允许在卡上退出免费期（x-a2n.trial=false）：想被网络发现，"
            "前 10 次完成的调用必须免费服务，额度用尽毕业之后才可收费")
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
    @staticmethod
    def _guard_selfproof(card: dict) -> None:
        """自证卡的闸门（注册 / 整卡更新共用）。

        两条都源自同一条纪律：**签名域是整张卡**。
          - 卡自称了身份（x-a2n.sovereign 有 did/pub/sig）就必须验得过——
            否则"平台里显示的 did"就成了可以随便写的一行字，冒名零成本。
          - 自签卡必须自带 uid：uid 是签名域的一部分，平台兜底补写会让签名
            当场失效（补一个字段 → 整卡哈希变 → 签名对不上）。平台不代它改卡。
        没声明身份的卡放行（它是"未自证"，不是"验不过"）——见 card_verdict 三态。
        """
        sov = sovereign_ext(card)
        if not any(sov.get(k) for k in ("did", "pub", "sig")):
            return
        ok, why = verify_selfproof(card)
        if not ok:
            raise ValidationError(f"卡自证不通过：{why}")
        if not (card.get("x-a2n") or {}).get("uid"):
            raise ValidationError(
                "自签卡必须自带 x-a2n.uid：uid 属于签名域，平台补写会让卡签名失效")

    def register(self, principal_id: str, card: dict, visibility: str = "public") -> dict:
        validate_card(card)
        self._guard_selfproof(card)
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
                json.dumps(deployment_of(ext), ensure_ascii=False),
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
        publish("node.registered", {"agent_id": agent_id, "card_hash": ch})
        c.commit()
        # 试用额度在注册那一刻定下（策略开关只在这里读一次环境变量）：
        # 想被发现的 agent 一律先免费服务 10 次（卡上没有退出通道，见 validate_card）；
        # 只有部署级开关 A2N_TRIAL_DEFAULT=0 才会直接记为已毕业（测试/演示用）。
        trial.open_for(agent_id, card)
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
        validate_card(card)   # 与三条上架路径同一形状闸门（url 可空 = 走中继门牌号）
        self._guard_selfproof(card)   # 自称了身份就必须验得过（冒名零成本的口子封在这）
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
             json.dumps(deployment_of(ext), ensure_ascii=False),
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
        # 读-改-写必须**整体**在 tx() 内：读在事务外的话，BEGIN IMMEDIATE 等锁
        # 期间 observe（使用端实测）提交的内容会被陈旧的 connection 副本写回 ——
        # 丢的不是锁里的数据，是"等锁时别人刚提交的"。读移到锁内才真正原子。
        # （实测复现：6 条观测与 30s 心跳撞车，丢 3 条。）
        with tx() as c:
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

    def credit(self, agent_id: str, amount: int, commit: bool = True) -> None:
        """节点完成任务计数与累计收益（agents 表归 registry，别的域只能经这里改）。

        commit=False 用于并入外层 store.tx()（提交权交出去）：
        “钱划走了、任务没到 SETTLED”这种半截事实不允许存在。
        """
        conn().execute(
            "UPDATE agents SET tasks_done = tasks_done + 1, earned = earned + ? WHERE agent_id=?",
            (amount, agent_id),
        )
        if commit:
            conn().commit()
            self.promote_if_ready(agent_id)
        # commit=False：晋升留在外层事务提交之后（它会自己开事务，不能嵌在中间）


registry = Registry()
