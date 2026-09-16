"""SDK 客户端：只用标准库，零依赖。"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from .errors import CallDeniedError, PaymentRequiredError


def _detail(raw: str) -> Any:
    """从错误响应体里取 FastAPI 的 detail（没有就原样返回文本）。"""
    try:
        body = json.loads(raw or "null")
    except ValueError:
        return raw[:300]
    if isinstance(body, dict) and "detail" in body:
        return body["detail"]
    return body


class Client:
    def __init__(self, base_url: str = "http://127.0.0.1:8000",
                 principal: str | None = None, node_id: str | None = None) -> None:
        self.base = base_url.rstrip("/")
        self.principal = principal
        self.node_id = node_id

    def _req(self, method: str, path: str, body: dict | None = None,
             headers: dict | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.principal:
            req.add_header("X-Principal", self.principal)
        if self.node_id:
            req.add_header("X-Node-Id", self.node_id)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode() or "null")
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            d = _detail(raw)
            # 402/403 是两根不同的指挥棒：一个"带钱重来"，一个"去补支付方式"。
            # 翻成类型化异常，调用方靠 except 判别，不用去解析文案。
            if e.code == 402:
                req_body = d.get("requirement") if isinstance(d, dict) else None
                raise PaymentRequiredError(req_body, f"{method} {path} -> 402 需要支付") from e
            if e.code == 403:
                msg = d.get("error") if isinstance(d, dict) else None
                hint = d.get("hint") if isinstance(d, dict) else None
                raise CallDeniedError(msg or f"{method} {path} -> 403 无调用资格", hint) from e
            raise RuntimeError(f"{method} {path} -> {e.code}: {raw[:300]}") from e

    # ---- 注册与发现 ----
    def register(self, card: dict, visibility: str = "public",
                 discover_limit: int | None = None) -> dict:
        """上架。

        discover_limit 是"允许被几个使用者发现"（None = 不限）：按使用者计名额、
        闲置即释放，压的是**同时**在用它的人数。它是分发策略，所以走请求参数、
        不写进卡 —— 平台改写卡会让供给方的自签当场失效（签名域=整张卡）。
        """
        body: dict = {"card": card, "visibility": visibility}
        if discover_limit is not None:
            body["discover_limit"] = discover_limit
        r = self._req("POST", "/v1/registry/agents", body)
        self.node_id = r["agent_id"]
        return r

    def set_listing(self, agent_id: str, visibility: str | None = None,
                    discover_limit: int | None = None) -> dict:
        """改上架信息：可见范围（public/unlisted/private）与允许被发现的数量。

        与 update_card 分开走：卡是供给方自签的**内容**（改它要重签、重算哈希），
        上架信息是**分发策略**（平台执行、平台记）。discover_limit 传 0 = 不限。
        """
        body: dict = {}
        if visibility is not None:
            body["visibility"] = visibility
        if discover_limit is not None:
            body["discover_limit"] = discover_limit
        return self._req("PUT", f"/v1/registry/agents/{agent_id}/listing", body)

    def update_card(self, agent_id: str, card: dict) -> dict:
        """整卡更新（改价/改算力/改结算方式）。

        只有主体自己能改自己的卡；uid 是全网唯一身份不可改；
        card_hash 同步重算，已成交快照不受影响，"下一单"看到新行情。
        """
        return self._req("PUT", f"/v1/registry/agents/{agent_id}/card", {"card": card})

    def get_agent(self, agent_id: str) -> dict:
        """取一个 agent 的完整卡（上架回填、调用前验价都用它）。"""
        return self._req("GET", f"/v1/registry/agents/{agent_id}")

    def heartbeat(self, connection: dict | None = None) -> dict:
        """心跳。connection 为节点自报（本机 IP 列表等），平台回传它的观测。"""
        assert self.node_id, "请先注册"
        return self._req("POST", f"/v1/registry/agents/{self.node_id}/heartbeat",
                         connection or {})

    def discover(self, skill: str, filt: dict | None = None, limit: int = 20) -> list[dict]:
        return self._req("POST", "/v1/discovery/query",
                         {"require": {"skill": skill}, "filter": filt, "limit": limit})

    # ---- 调用 agent（走治理链）----
    def call_token(self, agent_id: str, ttl: int = 600) -> str:
        """取**裸中继**凭据（底层原语用）。

        前提：与该 agent 存在 ACTIVE 对等账户配对，或该 agent 免费——因为
        `/v1/relay` 的收费语义只有对等账户。日常调用请用 `call_agent`（走
        治理链，对等账户/直付渠道/x402/免费都支持），不要直接用这个。
        """
        d = self._req("POST", "/v1/transport/call-token", {"agent_id": agent_id, "ttl": ttl})
        return d["token"]

    def call_agent(self, agent_id: str, skill: str = "", payload: Any = None,
                   currency: str | None = None, settle_points: bool = False,
                   message: dict | None = None, payment: str | None = None) -> dict:
        """调用一个 agent——与 A2A 入口**同一条治理链**（门禁 → 建任务 → 经
        通道执行 → 验收 → 记账）。

        门禁是"能力"而不是"配对"：免费直接放行；收费则看你有没有**任意一种
        可用支付方式**——对等账户配对过、或绑定了该 agent `accepts` 里的直付
        渠道（交集非空）、或该 agent 接受 x402。**对等账户是默认匹配，不是门槛**：
        agent 没声明 accepts 时按 peer_account 处理，但不会把别的可用方式一并否掉。

        绑定即自动结算：先用 `bind_pay_method(<渠道>)` 绑定一种直付渠道，之后
        调一个接受 `direct_pay:<同一渠道>` 的 agent 无需任何额外动作
        （渠道是自由字符串，具体品牌知识在持牌托管层，SDK 不认识任何品牌）。

        - `payment`：x402 的支付凭证（重试时带上）；该 agent 接受 x402 而你没带
          凭证时，会抛 `PaymentRequiredError`（带 `requirement` 挑战体）。
        - 返回规范结论字典：`task_id / state / ok / result / settle / capability ...`
        """
        body: dict = {"agent_id": agent_id, "skill": skill, "payload": payload,
                      "settle_points": settle_points}
        if currency:
            body["currency"] = currency
        if message is not None:
            body["message"] = message
        headers = {"X-PAYMENT": payment} if payment else None

        t0 = time.time()
        try:
            out = self._req("POST", "/v1/invoke", body, headers=headers)
        except Exception:
            self._report_obs(agent_id, int((time.time() - t0) * 1000), ok=False)
            raise
        self._report_obs(agent_id, int((time.time() - t0) * 1000), ok=True,
                         task_id=(out or {}).get("task_id"))
        return out

    def relay(self, agent_id: str, path: str = "", body: Any = None,
              token: str | None = None) -> Any:
        """**裸中继**（底层原语）：把一次请求直接转给节点本人的本地 HTTP 服务。

        与 `call_agent` 的区别：这里不经建任务/验收/公证，收费语义只有对等账户
        （见服务端 transport.py）。用它做"点对点打节点自定义路由"这类底层动作；
        面向"用别人的 agent"的调用请走 `call_agent`。

        凭据短命（默认 10 分钟），**过期自动重取一次**（服务端 401 = 凭据无效/
        过期，重取后重试；仍失败如实抛出）。
        """
        token = token or self.call_token(agent_id)
        sub = f"/{path.lstrip('/')}" if path else ""
        t0 = time.time()
        attempts = 0
        while True:
            attempts += 1
            try:
                out = self._req("POST", f"/v1/relay/{agent_id}{sub}", body or {},
                                headers={"X-A2N-Call": token})
            except RuntimeError as e:
                if "401" in str(e) and attempts == 1:
                    token = self.call_token(agent_id)   # 过期自动重取，只重试一次
                    continue
                self._report_obs(agent_id, int((time.time() - t0) * 1000), ok=False)
                raise
            except Exception:
                self._report_obs(agent_id, int((time.time() - t0) * 1000), ok=False)
                raise
            break
        self._report_obs(agent_id, int((time.time() - t0) * 1000), ok=True)
        return out

    def _report_obs(self, agent_id: str, total_ms: int, ok: bool,
                    task_id: str | None = None) -> None:
        try:
            self.observe(agent_id, task_id=task_id, total_ms=total_ms, ok=ok)
        except Exception:  # noqa: BLE001 - 观测回传绝不影响调用本身
            pass

    def observe(self, agent_id: str, task_id: str | None = None,
                rtt_ms: int | None = None, total_ms: int | None = None,
                ok: bool | None = None) -> dict:
        """回传使用端实测。绑 task_id 时平台严格校验任务两端，防刷。"""
        return self._req("POST", f"/v1/registry/agents/{agent_id}/observations",
                         {"task_id": task_id, "rtt_ms": rtt_ms,
                          "total_ms": total_ms, "ok": ok})

    # ---- 一期：多账户与对等账户 ----
    def create_account(self, label: str, ref: str | None = None) -> dict:
        """建一个结算账户。一个主体可以有多个（对公/对私/海外/不同业务线）。

        ref 是外部账户标识（对公账号等），A2N 不校验、只标注。
        """
        return self._req("POST", "/v1/party-accounts", {"label": label, "ref": ref})

    def accounts(self) -> list[dict]:
        return self._req("GET", "/v1/party-accounts")

    def propose_peer(self, account_id: str, agent_id: str, terms: dict | None = None,
                     peer_ref: str | None = None, auto_accept: bool = True) -> dict:
        """与 agent 建立对等账户。条款谈妥并 ACTIVE 后才可能达成交易。"""
        return self._req("POST", "/v1/peers", {"account_id": account_id, "agent_id": agent_id,
                                               "terms": terms, "peer_ref": peer_ref,
                                               "auto_accept": auto_accept})

    def accept_peer(self, link_id: str) -> dict:
        return self._req("POST", f"/v1/peers/{link_id}/accept", {})

    def close_peer(self, link_id: str) -> dict:
        return self._req("POST", f"/v1/peers/{link_id}/close", {})

    def peers(self, account_id: str | None = None) -> list[dict]:
        path = f"/v1/peers?account_id={account_id}" if account_id else "/v1/peers"
        return self._req("GET", path)

    def peer_usable(self, link_id: str) -> dict:
        """还能不能做交易：ACTIVE 且未超额度。"""
        return self._req("GET", f"/v1/peers/{link_id}/usable")

    # ---- 一期：交易与对账 ----
    def open_deal(self, link_id: str, skill: str, task_id: str | None = None) -> dict:
        """达成交易：条款当场快照冻结，事后改条款不影响这一单。"""
        return self._req("POST", "/v1/deals", {"link_id": link_id, "skill": skill,
                                               "task_id": task_id})

    def report_deal(self, deal_id: str, party: str, dims: dict,
                    amount_fen: int | None = None, evidence: dict | None = None) -> dict:
        """上报计量。双方各报各的——A2N 只记录，不替任何一方下结论。

        amount_fen 留空时按成交条款的单价算。
        """
        return self._req("POST", f"/v1/deals/{deal_id}/report",
                         {"party": party, "dims": dims, "amount_fen": amount_fen,
                          "evidence": evidence})

    def reconcile_deal(self, deal_id: str) -> dict:
        """对账。一致→RECONCILED；分歧→DISPUTED 且认定金额取较小者。"""
        return self._req("POST", f"/v1/deals/{deal_id}/reconcile", {})

    def deals(self, link_id: str) -> list[dict]:
        return self._req("GET", f"/v1/peers/{link_id}/deals")

    def issue_statement(self, link_id: str, period: str | None = None) -> dict:
        """出账（幂等：同账期重复调用返回同一张单）。"""
        return self._req("POST", "/v1/statements", {"link_id": link_id, "period": period})

    def statements(self, link_id: str | None = None) -> list[dict]:
        path = f"/v1/statements?link_id={link_id}" if link_id else "/v1/statements"
        return self._req("GET", path)

    # ---- 任务 ----
    def create_task(self, skill: str, payload: dict | None = None, budget: int = 100,
                    preferred_agents: list[str] | None = None) -> dict:
        return self._req("POST", "/v1/tasks", {"skill": skill, "payload": payload,
                                               "budget": budget, "preferred_agents": preferred_agents})

    def pending_tasks(self, wait: float = 0.0) -> list[dict]:
        """取活。wait>0 = 长轮询：没有任务就挂着，来了立即返回。

        家宽电脑没有公网入口也能跑 —— 我们从不被调用，我们只自己来取。
        """
        assert self.node_id
        return self._req("GET", f"/v1/nodes/{self.node_id}/tasks?wait={min(wait, 30)}")

    def submit(self, task_id: str, result: Any, usage: dict) -> dict:
        return self._req("POST", f"/v1/tasks/{task_id}/result", {"result": result, "usage": usage})

    def fail(self, task_id: str, reason: str = "执行失败或超时") -> dict:
        """节点自报执行失败：任务走 FAILED 终态，冻结预算原路退回。

        没上报失败的节点会把任务永远留在 ASSIGNED —— 使用方的钱冻着、
        任务单停在半路。干不成必须说出来（与"干了没过验收"是两回事）。
        """
        assert self.node_id, "请先注册"
        return self._req("POST", f"/v1/tasks/{task_id}/fail", {"reason": reason})

    def cancel_task(self, task_id: str, reason: str = "使用方取消") -> dict:
        """取消任务：冻结的预算原路退回（只有发起方能取消）。"""
        return self._req("POST", f"/v1/tasks/{task_id}/cancel", {"reason": reason})

    def a2a_state(self, task_id: str) -> dict:
        """标准 A2A v1.0 Task 视图 —— 与任意 A2A 兼容方对话时用这个。"""
        return self._req("GET", f"/v1/tasks/{task_id}/a2a")

    # ---- 本地市场列表（按需拉取 + 本地沉淀）----
    def sync_market(self, skill: str, path: str | None = None, limit: int = 20) -> list[dict]:
        """把一次发现的结果存成本地市场列表文件。

        "发现"是一次网络动作，"市场列表"是本机沉淀 —— 之后离线也能看、
        也能选。没人同步全量目录，你的市场是你自己问出来的那一份。
        """
        found = self.discover(skill, limit=limit)
        if path is None:
            import os
            path = os.path.join(os.path.expanduser("~"), ".a2n",
                                f"market-{skill.replace('/', '_')}.json")
        import os
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            import json
            json.dump({"skill": skill, "synced_at": int(time.time()), "agents": found},
                      f, ensure_ascii=False, indent=1)
        return found

    # ---- 支付方式（直付渠道绑定：选择绑定账户走引导，绑定即自动结算）----
    def pay_channels(self) -> dict:
        """可绑定渠道清单 + 各自引导页 schema（渠道知识在持牌层）。"""
        return self._req("GET", "/v1/pay-methods/channels")

    def pay_methods(self, channel: str | None = None) -> list[dict]:
        """我已绑定的支付方式（ref 已脱敏，返回 masked_ref）。"""
        path = f"/v1/pay-methods?channel={channel}" if channel else "/v1/pay-methods"
        return self._req("GET", path)

    def bind_pay_method(self, channel: str, fields: dict | None = None,
                        ref: str | None = None, currency: str | None = None) -> dict:
        """绑定一个直付渠道。fields 按渠道引导 schema 校验（优先），
        ref 是老式单凭据兼容口。绑定 ACTIVE 即自动结算——调用时门禁自动选用。"""
        return self._req("POST", "/v1/pay-methods",
                         {"channel": channel, "fields": fields, "ref": ref,
                          "currency": currency})

    def close_pay_method(self, pm_id: str) -> dict:
        return self._req("POST", f"/v1/pay-methods/{pm_id}/close", {})

    def pay_compatible(self, agent_id: str) -> dict:
        """我与某个 agent 的支付能力交集：差哪样、补哪样，一次说清。"""
        return self._req("GET", f"/v1/pay-methods/compatible?agent_id={agent_id}")

    def charge(self, charge_id: str) -> dict:
        """直付/x402 成交凭证全文 + 机器核验（金额/授权链/任务一致性/单价漂移）。"""
        return self._req("GET", f"/v1/pay-charges/{charge_id}")

    # ---- AP2 集成（翻译层在服务端，SDK 只做调用）----
    def ap2_budget(self, intent: dict, skill: str, price_hint_fen: int | None = None) -> dict:
        """把 AP2 Intent 授权翻译成 A2N 冻结预算参数（可直接喂给 create_task）。"""
        return self._req("POST", "/v1/ap2/budget",
                         {"intent": intent, "skill": skill, "price_hint_fen": price_hint_fen})

    def ap2_receipt(self, task_id: str) -> dict:
        """结算后取 AP2 可消费的结算凭证（回填 Payment Mandate 问责链）。"""
        return self._req("POST", "/v1/ap2/receipt", {"task_id": task_id})

    # ---- 资金（程序化调用）----
    def deposit(self, account_id: str, amount_fen: int) -> dict:
        """模拟持牌方充值回调（生产由持牌方调用）。"""
        return self._req("POST", "/v1/custodian/deposit",
                         {"account_id": account_id, "amount_fen": amount_fen})

    def withdraw(self, amount: int) -> dict:
        return self._req("POST", "/v1/wallet/withdraw", {"amount": amount})

    def balance(self, account_id: str) -> int:
        return self._req("GET", f"/v1/accounts/{account_id}/balance")["points"]

    def meter(self, started: float, **dims) -> dict:
        """构造计量上报：SDK 侧的内部真相。"""
        dims.setdefault("call_count", 1)
        dims["wall_time_ms"] = int((time.time() - started) * 1000)
        return dims
