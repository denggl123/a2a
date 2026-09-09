"""SDK 客户端：只用标准库，零依赖。"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any


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
            raise RuntimeError(f"{method} {path} -> {e.code}: {e.read().decode()[:300]}") from e

    # ---- 注册与发现 ----
    def register(self, card: dict, visibility: str = "public") -> dict:
        r = self._req("POST", "/v1/registry/agents", {"card": card, "visibility": visibility})
        self.node_id = r["agent_id"]
        return r

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

    # ---- 调用 agent（走门禁）----
    def call_token(self, agent_id: str, ttl: int = 600) -> str:
        """取调用凭据。前提：与该 agent 存在 ACTIVE 对等账户配对。"""
        d = self._req("POST", "/v1/transport/call-token", {"agent_id": agent_id, "ttl": ttl})
        return d["token"]

    def call_agent(self, agent_id: str, path: str = "", body: Any = None,
                   token: str | None = None) -> Any:
        """点对点调用一个 agent。

        固定两步：先取凭据（服务端校验配对关系），再打平台中继入口。
        使用方从头到尾接触不到节点的真实地址——发现结果里的 url
        就是 A2N 自己的门牌号。凭据短命（默认 10 分钟），过期自动重取。
        """
        token = token or self.call_token(agent_id)
        sub = f"/{path.lstrip('/')}" if path else ""
        return self._req("POST", f"/v1/relay/{agent_id}{sub}", body or {},
                         headers={"X-A2N-Call": token})

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
