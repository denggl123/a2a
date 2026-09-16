"""M3 任务域：状态机 + 预算冻结 + 结果接收。

跨模块只发事件，不直接调用下游服务的写操作（事件编排）。
"""
from __future__ import annotations

import json
import time
from typing import Any

from a2n_store import conn, tx
from a2n_kernel.events import publish
from a2n_kernel.hashing import new_id, now_iso, sha256
from a2n_acceptance import judge, parse_template, policy_ref
from a2n_dispatch import discovery
from a2n_p2p import card_pub_raw, pub_b64, verify_metering
from a2n_registry import registry, seats, trial
from a2n_ledger import Ledger, ensure_account
from a2n_reputation import apply_event
from a2n_settlement import (BILLABLE_DIMS, closing, compute_amount, entries_of,
                            is_billable, quote, register_dimension, settlement)
from .a2a import a2a_view, can_transition, to_a2a

STATE_FLOW = ["CREATED", "ASSIGNED", "SUBMITTED", "ACCEPTED", "SETTLED"]


class Tasks:
    def __init__(self) -> None:
        self.ledger = Ledger()

    # ---------- 状态机 ----------
    def _transition(self, task_id: str, new_state: str, commit: bool = True,
                   **extra: Any) -> None:
        """唯一改状态的入口。

        以前是直接 UPDATE，状态机只存在于注释里 —— 于是"已结算的任务又被
        打成已拒绝"在代码上是允许的。现在非法迁移直接抛错，
        A2A 状态映射也只认这里出去的状态。
        """
        row = conn().execute("SELECT state FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise ValueError("任务不存在")
        cur = row["state"]
        if cur == new_state:
            return
        if not can_transition(cur, new_state):
            raise ValueError(f"非法状态迁移：{cur} → {new_state}")
        fields = {"state": new_state, "updated_at": now_iso(), **extra}
        sets = ", ".join(f"{k}=?" for k in fields)
        conn().execute(f"UPDATE tasks SET {sets} WHERE id=?", (*fields.values(), task_id))
        if commit:      # commit=False：提交权交给外层 store.tx()（与资金动作同生共死）
            conn().commit()

    def a2a(self, task_id: str) -> dict | None:
        """对外视角：标准 A2A v1.0 Task 对象。"""
        t = self.get(task_id)
        return a2a_view(t) if t else None

    def create(self, requester_id: str, skill: str, payload: dict | None, budget: int,
               preferred_agents: list[str] | None = None, source: str = "native",
               hold_budget: bool = True, delivery: str = "dispatch",
               currency: str = "CNY") -> dict[str, Any]:
        """建任务。

        budget      合约价上限（分）—— 用于验收金额封顶与"事后涨价无效"，
                    与"是否真冻结 A2N 积分"是两件事。
        hold_budget 是否真冻结积分预算。**只有预付费积分模式才为 True**；
                    一期对等账户（双边记账）与直付/x402（钱走外部渠道）都不
                    动 A2N 积分，传 False —— 否则会在账本上凭空多出冻结流水。
        source      任务来源（native 平台派单 / a2a 标准协议入口），用于区分
                    入口而不改变后续验收与记账规则：入口可以变，规矩不能变。
        delivery    投递方式，决定任务怎么到节点手上：
                      dispatch 节点取活 —— 推送实时下发 + 长轮询兜底（平台派单流）；
                      inline   随调用就地交付 —— 调用方经通道转发执行、并代节点
                               提交结果（A2A 网关流）。**inline 任务绝不进派单
                               通道**：否则同一份活会被"推送/取活/转发"三条通道
                               抢着干，重复执行就是重复计费。
        """
        if delivery not in ("dispatch", "inline"):
            raise ValueError(f"未知投递方式：{delivery}")
        ensure_account(requester_id, "user", requester_id)

        # 锁定合约价：从候选 card 的 price_hint 取，写入任务单，节点事后涨价无效
        # 只把任务派给此刻真正送得进去的节点（NAT 后未建通道的不派）
        # viewer=requester_id：名额满了的 agent 对新使用者不再可见，但已在用的人
        # （名额算他的）仍能继续发现与下单 —— 看不见自己的在用对象才是坏体验。
        candidates = discovery.query({"skill": skill}, limit=10, include_unlisted=True,
                                     viewer=requester_id)
        if preferred_agents:
            # 点名派单：只允许在点名节点里选（扩大查询避免被挤掉）。
            # 点名是使用方的意志，不是"优先建议" —— 静默换人等于把预算冻给了
            # 一个使用方从没听说过的节点。
            named = [c for c in discovery.query({"skill": skill}, limit=200,
                                                include_unlisted=True, viewer=requester_id)
                     if c["agent_id"] in preferred_agents]
            if named:
                candidates = named
        reachable_first = [c for c in candidates if c.get("reachable")]
        if reachable_first:
            candidates = reachable_first
        if not candidates:
            # "没有候选"有两种，行动指令完全不同，不能糊成一句：
            #   ① 全网真没有这个能力 → 换能力；
            #   ② 有，但此刻都不接新使用者（名额满了 / 状态不可派单 / 不可达）
            #      → 稍后再试或换个节点。
            # 所以先点一下该能力下到底有没有节点，再决定怎么说。
            total = conn().execute(
                "SELECT COUNT(DISTINCT agent_id) n FROM skills WHERE skill_id=?",
                (skill,)).fetchone()["n"]
            if total:
                raise ValueError(
                    f"具备能力 {skill} 的节点有 {total} 个，但此刻都不接新使用者："
                    f"可能是上架名额已满（名额按使用者计、闲置会自动释放），"
                    f"也可能是状态不可派单/不可达 —— 稍后再试，或换一个能力")
            raise ValueError(f"没有找到具备能力 {skill} 的节点")
        chosen = candidates[0]

        ok, why = discovery.assignable(chosen["agent_id"], chosen["card_hash"],
                                       principal=requester_id)
        if not ok:
            raise ValueError(f"候选节点不可派单：{why}")

        hint = (chosen.get("price_hint") or {}).get(skill) or {}
        # 锁定合约价（v2 优先）：agent 的价目表是它自己声明的行情，这里只做
        # 快照冻结 —— 币种、维度、单价、per 成交那一刻锁死，事后改价无效。
        # 没报价 = 0（免费），不是 1。A2N 只发行情不定价：节点没标价就替它
        # 定一个默认价，等于平台替供给方做了定价决定 —— 越权且不可审计。
        chosen_card = json.loads((registry.get(chosen["agent_id"]) or {}).get("card_json") or "{}")
        cur = (currency or "CNY").upper()
        entries = entries_of(chosen_card, skill, cur)
        if entries:
            unit_prices = entries                      # v2：[{key, amount, per}]
        else:
            unit_prices = {"call_count": float(hint.get("amount", 0))}   # v1：{维度: 分}

        # 试用期：未毕业的 agent 一律按免费处理（"毕业之后才允许收费"是铁律）。
        # 价目快照**在这里就地归一为 0 单价**，而不是在计费处到处特判：
        # 后面验收/记账看到的都是"合法的免费调用"（有计量、单价 0），
        # 而不是"计量缺失"，也不需要各处认试用这个特例。
        in_trial = trial.in_trial(chosen["agent_id"])
        if in_trial:
            unit_prices = {"call_count": 0.0}
        # 这一段额度是哪来的（首装 / 重连采样）在**建单这一刻冻住**：重连额度是给节点
        # 重新上线采网络稳定参数用的，落进它的案例不进质量模板 —— 事后补额、毕业
        # 都不改写这一单的性质（快照，不是实时查询）。
        trial_kind = trial.current_kind(chosen["agent_id"]) if in_trial else None

        task_id = new_id("t")
        ts = now_iso()
        payload_hash = sha256(json.dumps(payload or {}, ensure_ascii=False, sort_keys=True))

        # 预算托管账户先建好：ensure_account 自带提交，不能出现在下面的写事务里
        if hold_budget and budget > 0:
            hold = f"hold:{task_id}"
            ensure_account(hold, "hold", hold)

        # 任务落库 + 派单 + 预算冻结 + 事件 outbox：一个事务。
        # publish 必须在 commit 之前（kernel.events 的语义）：中途崩溃时
        # 业务与事件一起回滚——不留"任务建了、事件没了"的窗口。
        with tx():
            conn().execute(
                "INSERT INTO tasks (id, requester_id, skill_id, source, payload_hash, payload,"
                " budget, unit_prices, card_hash, state, node_id, delivery, trial, trial_kind,"
                " created_at, updated_at, currency, amount_minor, budget_minor)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (task_id, requester_id, skill, source, payload_hash,
                 json.dumps(payload or {}, ensure_ascii=False),
                 budget, json.dumps(unit_prices), chosen["card_hash"], "CREATED",
                 chosen["agent_id"], delivery, 1 if in_trial else 0, trial_kind, ts, ts,
                 (currency or "CNY").upper(), 0, budget),
            )
            self._transition(task_id, "ASSIGNED", commit=False)

            # 上架名额：在"建立调用关系"这一刻占一个，**同一笔事务**。
            # 与任务同生共死：任务没落库就不留"占了名却没调用"的空档；
            # 抢不到（并发撞车 / 刚好满员）就整体回滚 —— 预算也不会冻上。
            # 名额按使用者计、闲置即释放（a2n_registry.seats）：
            # 它压的是**同时**在用它的人数，不是累计用过的人次。
            got_seat, seat = seats.take(chosen["agent_id"], requester_id, task_id,
                                        commit=False)
            if not got_seat:
                raise ValueError(f"候选节点名额已满：{seat['reason']}")

            # 冻结预算：只有真走 A2N 积分时才冻结（见 create 的 hold_budget）
            if hold_budget and budget > 0:
                hold = f"hold:{task_id}"
                self.ledger.post(requester_id, -budget, "freeze", task_id, commit=False)
                self.ledger.post(hold, budget, "freeze", task_id, commit=False)

            publish("task.created", {"task_id": task_id, "skill": skill, "budget": budget})
            publish("task.assigned", {"task_id": task_id, "node_id": chosen["agent_id"],
                                      "unit_prices": unit_prices, "card_hash": chosen["card_hash"]})

        # 派单通道只服务 dispatch 任务：inline 任务由调用方自己交付，
        # 推下去 = 两条通道各干一遍 = 重复执行重复计费。
        if delivery == "dispatch":
            from a2n_transport import hub
            if hub.alive(chosen["agent_id"]):
                hub.push(chosen["agent_id"], {"type": "task", **self.get(task_id)})

        return self.get(task_id)

    def get(self, task_id: str) -> dict | None:
        r = conn().execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["unit_prices"] = json.loads(d["unit_prices"])
        return d

    def pending_for(self, node_id: str, wait: float = 0.0, limit: int = 5) -> list[dict]:
        """节点取活。

        wait>0 时挂起等待（长轮询）：家宽节点没有公网入口，只能自己来取，
        长轮询把"取"的延迟从轮询间隔压到近乎为零，且不引入任何入站通道。
        """
        deadline = time.time() + max(0.0, min(wait, 30.0))
        while True:
            # 取活只吐 dispatch 任务（长轮询是派单通道的兜底）；
            # inline 任务随调用就地交付，出现在这里就会被节点再干一遍。
            rows = conn().execute(
                "SELECT * FROM tasks WHERE node_id=? AND state='ASSIGNED'"
                " AND delivery='dispatch' ORDER BY rowid LIMIT ?",
                (node_id, limit)
            ).fetchall()
            if rows or time.time() >= deadline:
                out = []
                for r in rows:
                    d = dict(r)
                    d["unit_prices"] = json.loads(d["unit_prices"])
                    if d.get("payload"):
                        d["payload"] = json.loads(d["payload"])
                    out.append(d)
                return out
            time.sleep(0.3)

    def submit(self, task_id: str, node_id: str, result: Any, usage: dict,
               settle: bool = True) -> dict[str, Any]:
        """节点提交结果：算结果哈希 → 计量入账 → 过验收 →（可选）积分结算。

        settle=False：停在 ACCEPTED，**不做 A2N 积分结算**。
        一期对等账户（双边记账、按账期结清）与直付/x402（钱走外部渠道）
        都不动 A2N 积分，记账由各自的结算方式负责（deals / pay_charges）。
        验收照做 —— 换了结算方式，验收这道关不能省。
        """
        task = self.get(task_id)
        if not task:
            raise ValueError("任务不存在")
        if task["node_id"] != node_id:
            raise ValueError("无权提交该任务")
        if task["state"] != "ASSIGNED":
            raise ValueError(f"任务状态不允许提交：{task['state']}")

        result_hash = sha256(json.dumps(result, ensure_ascii=False, sort_keys=True) if not isinstance(result, str)
                             else result)
        agent = registry.get(node_id) or {}
        card = json.loads(agent.get("card_json") or "{}")
        # attest 是"签在计量上的信封"，不是计量维度本身 —— 先摘出来，别让它
        # 混进计量内容（混进去就会既过不了签名的内容核对，也会被当成一个怪维度）。
        usage = dict(usage or {})
        att = usage.pop("attest", None)
        # 验收上下文：把"这次交付"与"该 agent 声明的验收模板"一并交给策略，
        # 模板偏差（质量硬指标）由此算出 —— 验收策略的函数签名一个字不改。
        task_ctx = {"sla": agent.get("sla") or {},
                    "template": parse_template(card),
                    "delivery": result}

        verdict = judge(task_ctx, usage, result_hash, time.time())
        dims = usage.get("dims", usage)
        # 金额计算两代价目并存：v2 条目快照走 quote 引擎（多维度叠加、per、
        # 末次取整、预算封顶）；v1 {维度: 单价} 走 compute_amount。计费维度
        # 都以注册表为准（is_billable），注册表说了算，代码不认识具体维度。
        up = task["unit_prices"]
        if isinstance(up, list):
            amount = quote(up, dims, task["budget"])["amount_minor"]
            billable_keys = [e["key"] for e in up if is_billable(e["key"])]
        else:
            amount = compute_amount(up, dims, task["budget"])
            billable_keys = [d for d in up if is_billable(d)]
        # "金额为 0" 有两种完全不同的含义，必须分开判：
        #   ① 一个可计费维度都没上报 = 计量缺失 → 打回（没有计量就没有账）
        #   ② 有计量但单价就是 0 = 合法的免费调用 → 放行
        # 以前一刀切判失败，免费 agent 会被误打成"计量缺失"。
        reported = [d for d in billable_keys if float(dims.get(d, 0) or 0) > 0]
        if not reported:
            verdict = {**verdict, "passed": False,
                       "reasons": (verdict.get("reasons") or [])
                       + ["没有可计费的计量维度：自报维度均不在可计费清单内"]}

        cur = task.get("currency") or "CNY"
        usage_id = new_id("u")
        observed = {"wall_time_ms": verdict.get("observed_ms")}

        # 计量验签：验过的计量才敢说"这是节点签的"。四步缺一不可（形状 → 身份自洽 →
        # 内容确实为这一单 → 签名）；只验签不核对内容，等于允许拿一单的签名替另一单背书。
        # 验不过 → 进"争议"，不许叫"已对账"；没签名 → 如实记为未签名，不假装验过。
        att_ok, att_reason = False, "节点未签名：只有平台观测 vs 自报的比对，不是可复算的证据"
        if att is not None:
            ok, why = verify_metering(att, expect_task_id=task_id, expect_node_id=node_id,
                                      expect_dims=dims)
            # 再加一步**归属**：签名只证明"某个持钥匙的人签了这些内容"，不证明"签的人
            # 就是这个节点"。归属只能靠卡上声明的公钥来认 —— 卡没声明时必须拦下：
            # 放行等于任何一把钥匙都能替这个节点背书，"已对账"就成了空话。
            card_pub = card_pub_raw(card)
            if ok and card_pub is None:
                ok, why = False, "卡上没有声明身份公钥：这份签名无从归属到本节点"
            elif ok and att.get("pub") != pub_b64(card_pub):
                ok, why = False, "计量签名用的钥匙与卡上声明的不是同一把"
            att_ok, att_reason = ok, ("验签通过" if ok else why)
        status = "reconciled" if verdict["passed"] else "disputed"
        if att is not None and not att_ok:
            status = "disputed"          # 签名不实的计量，不许进"已对账"
        dev = verdict.get("deviation") or {}
        tref = verdict.get("template_ref")

        # 提交 → 验收 →（可选）结算：状态推进、计量入库、验收结论、事件
        # outbox 全部一个事务（publish 一律在 commit 前）。任何一步崩溃整段
        # 回滚，不留"状态推进了、事件没了"或"钱划了、任务没终态"的半截事实。
        #
        # 结算收口（P1 §3.2）：结果由 a2n_settlement.closing 统一落账 ——
        # 两条记账路**同一个触发点**、task_id 作幂等键。而"失败了要看得见"：
        # 事务一旦回滚，事务内补写的待处理也会跟着消失（= 静默失败），
        # 所以待处理事实在**事务外**补记。
        so: dict | None = None
        settle_fail: str | None = None
        try:
            with tx():
                self._transition(task_id, "SUBMITTED", commit=False,
                                 result_hash=result_hash,
                                 result=json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result,
                                 amount=amount, amount_minor=amount, currency=cur)
                publish("task.submitted", {"task_id": task_id, "node_id": node_id, "result_hash": result_hash,
                                           "amount": amount, "currency": cur})
                # 记录计量（双向对账 + 节点签名 + 模板偏差）
                conn().execute(
                    "INSERT INTO usage_reports (usage_id, task_id, node_id, dims, observed, variance, result_hash,"
                    " contract, signature, status, attest, attested, attest_reason, quality, template_ref,"
                    " deviation, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (usage_id, task_id, node_id, json.dumps(dims, ensure_ascii=False),
                     json.dumps(observed, ensure_ascii=False), verdict.get("variance"), result_hash,
                     json.dumps({"unit_prices": task["unit_prices"], "card_hash": task["card_hash"]}, ensure_ascii=False),
                     (att or {}).get("sig") or "", status,
                     json.dumps(att, ensure_ascii=False) if att is not None else None,
                     1 if att_ok else 0, att_reason,
                     verdict.get("quality"),
                     json.dumps(tref, ensure_ascii=False) if tref else None,
                     json.dumps(dev, ensure_ascii=False) if dev else None, now_iso()),
                )
                if not verdict["passed"]:
                    publish("acceptance.failed", {"task_id": task_id, "node_id": node_id,
                                                  "reasons": verdict["reasons"]})
                    # 打回：有冻结才退（没冻结说明根本没走 A2N 积分）
                    hold = f"hold:{task_id}"
                    bal = self.ledger.balance(hold)
                    if bal > 0:
                        self.ledger.post(hold, -bal, "refund", task_id, commit=False)
                        self.ledger.post(task["requester_id"], bal, "refund", task_id, commit=False)
                    self._transition(task_id, "REJECTED", commit=False,
                                     reject_reason="; ".join(verdict["reasons"]))
                else:
                    publish("acceptance.passed", {"task_id": task_id, "node_id": node_id,
                                                  "score": verdict["score"]})
                    self._transition(task_id, "ACCEPTED", commit=False)
                    if settle:
                        # 钱与状态必须同生共死：划转、记账、终态、结算收口一个事务，
                        # 中途崩溃整段回滚（否则钱划走了任务却停在 ACCEPTED，重试即二次结算）。
                        try:
                            so = settlement.settle(task_id, task["requester_id"], node_id, amount,
                                                   currency=cur, commit=False)
                            registry.credit(node_id, amount, commit=False)
                            self._transition(task_id, "SETTLED", commit=False)
                            closing.record(task_id, closing.MODE_POINTS, amount, cur,
                                           ref=(so or {}).get("so_id"), commit=False)
                        except Exception as e:   # noqa: BLE001 - 如实记下，绝不吞
                            settle_fail = f"{type(e).__name__}: {e}"
                            raise
        except Exception:
            if settle_fail:
                # 事务已回滚：在事务外补记待处理，让这次失败**留在账上**。
                # 静默失败比失败本身更危险 —— 使用方以为结完了，账上却没有这笔。
                closing.mark_pending(task_id, closing.MODE_POINTS, settle_fail,
                                     amount_minor=amount, currency=cur)
            raise

        # 声誉/晋升/试用额度各自开事务，放在事务外——派生数据不参与资金原子性
        if not verdict["passed"]:
            apply_event(node_id, "acceptance.failed")
            return {"task_id": task_id, "passed": False, "reasons": verdict["reasons"]}
        apply_event(node_id, "acceptance.passed")
        # 试用额度：只有**完成**的调用吃额度（失败/超时/退回都不计，别让失败吃掉额度）。
        # 谁调的都算 —— 额度不按人计；自源调用照吃额度，但不进公开证据（计数与证据分开算）。
        if task.get("trial"):
            trial.consume(node_id)
        if not settle:
            # 积分之外的结算方式：验收已过、金额已定，剩下的账交给结算层。
            # 这里刻意不推进到 SETTLED —— 没走积分就不许说"已结算"。
            return {"task_id": task_id, "passed": True, "amount": amount,
                    "verdict": verdict, "settlement": None}
        apply_event(node_id, "settlement.settled")
        registry.promote_if_ready(node_id)
        return {"task_id": task_id, "passed": True, "amount": amount, "settlement": so, "verdict": verdict}

    def cancel(self, task_id: str, requester_id: str, reason: str = "使用方取消") -> dict:
        """取消：只有发起方能取消，且必须把钱原路退回去。

        取消不是"删掉这条记录" —— 记录留着，状态走到终态 CANCELED，
        冻结的钱退回。审计要看的是"发生过什么"，不是"现在是什么样"。
        """
        task = self.get(task_id)
        if not task:
            raise ValueError("任务不存在")
        if task["requester_id"] != requester_id:
            raise ValueError("只有发起方能取消任务")
        hold = f"hold:{task_id}"
        # 退款 + 终态 + 事件一个事务（publish 在 commit 前，崩溃不丢事件）
        with tx():
            bal = self.ledger.balance(hold)
            if bal:
                self.ledger.post(hold, -bal, "refund", task_id, commit=False)
                self.ledger.post(requester_id, bal, "refund", task_id, commit=False)
            self._transition(task_id, "CANCELED", commit=False, fail_reason=reason)
            publish("task.canceled", {"task_id": task_id, "refunded": bal, "reason": reason})
        return {"task_id": task_id, "state": "CANCELED", "refunded": bal}

    def fail(self, task_id: str, reason: str = "执行失败或超时") -> dict:
        """节点侧失败/超时：与 REJECTED（验收不通过）分开，便于区分责任。

        REJECTED = 干了，但干得不合规；FAILED = 根本没干完。
        两者都全额退款，但记账理由不同，信誉扣分也不同。
        """
        task = self.get(task_id)
        if not task:
            raise ValueError("任务不存在")
        hold = f"hold:{task_id}"
        # 退款 + 终态 + 事件一个事务；声誉扣分（自开事务）留到事务外
        with tx():
            bal = self.ledger.balance(hold)
            if bal:
                self.ledger.post(hold, -bal, "refund", task_id, commit=False)
                self.ledger.post(task["requester_id"], bal, "refund", task_id, commit=False)
            self._transition(task_id, "FAILED", commit=False, fail_reason=reason)
            publish("task.failed", {"task_id": task_id, "refunded": bal, "reason": reason})
        if task.get("node_id"):
            apply_event(task["node_id"], "task.failed")
        return {"task_id": task_id, "state": "FAILED", "refunded": bal}

    def list(self, requester_id: str | None = None, node_id: str | None = None, limit: int = 50) -> list[dict]:
        if requester_id:
            rows = conn().execute("SELECT * FROM tasks WHERE requester_id=? ORDER BY rowid DESC LIMIT ?",
                                  (requester_id, limit)).fetchall()
        elif node_id:
            rows = conn().execute("SELECT * FROM tasks WHERE node_id=? ORDER BY rowid DESC LIMIT ?",
                                  (node_id, limit)).fetchall()
        else:
            rows = conn().execute("SELECT * FROM tasks ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


tasks = Tasks()
