"""A2A 协议层端到端冒烟（发现开放 + 直付/对等双结算通道 + 合约核验）。

链路（一次跑完，全部走真实 HTTP）：
  ① 开放发现：零准备也能搜到收费与免费 agent；peer-ready 只是可选便利筛选
  ② 免费调用：使用方零准备直接调免费 agent → completed，不产生任何账
  ③ 收费零准备：直接调收费 agent → -32001，错误信息列全"对方收什么、怎么补"
  ④ 直付：alice 登记支付宝（POST /v1/pay-methods）→ 直接就能调 → CAPTURED 凭证
  ⑤ 合约核验：GET /v1/pay-charges/{id} 重验授权链/金额/任务一致性/单价漂移
  ⑥ 对等账户：dave 建账户+配对 → 调同一个收费 agent → deal RECONCILED
  ⑦ tasks/get 复查

前置：平台(A2N_DB=data/e2e_a2a.db) + 收费节点(9102) + 免费节点(9103) 都在跑。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
ALICE = "acct:alice"
DAVE = "acct:dave"

# demo 四节点的展示名（与 scripts/run_a2a_node.py 的 PRESETS 对应；
# 按角色改名时这里要一起改，否则冒烟找不到节点）
N_CHARGING = "华东-精算OCR"
N_FREE = "华北-公益OCR"
N_X402 = "新加坡-极速OCR"

from a2n_custodian import encode_payment  # noqa: E402 - 脚本里现成的 x402 编码工具


def req(method: str, path: str, body: dict | None = None,
        principal: str | None = None, payment: str | None = None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        r.add_header("Content-Type", "application/json")
    if principal:
        r.add_header("X-Principal", principal)
    if payment:
        r.add_header("X-PAYMENT", payment)
    try:
        with urllib.request.urlopen(r, timeout=40) as resp:
            payload = resp.read().decode()
            return json.loads(payload) if payload else None
    except urllib.error.HTTPError as e:
        # 402 这类"带语义的状态码"要能读到响应体，不能当成网络错误；
        # 平台 500 的 HTML 错误页也要如实呈现，而不是让脚本自己崩掉
        payload = e.read().decode()
        try:
            out = json.loads(payload) if payload else {}
        except ValueError:
            out = {"__raw": payload[:200]}
        if isinstance(out, dict):
            out["__status"] = e.code
        return out


def send(agent_id: str, text: str, skill: str, principal: str, rid: int = 1,
         payment: str | None = None):
    rpc = {"jsonrpc": "2.0", "id": rid, "method": "message/send", "params": {
        "message": {"role": "user", "parts": [{"kind": "text", "text": text}]},
        "metadata": {"skill": skill}}}
    return req("POST", f"/a2a/{agent_id}", rpc, principal=principal, payment=payment)


def main() -> int:
    fails = []

    def check(name: str, cond: bool, extra: str = "") -> None:
        mark = "✓" if cond else "✗"
        print(f"  {mark} {name}" + (f"  {extra}" if extra else ""))
        if not cond:
            fails.append(name)

    print("① 开放发现：零准备也能搜（发现不设门槛）")
    all_hits = req("POST", "/v1/discovery/query", {"require": {"skill": "ocr-pro"}})
    charging = next((a for a in all_hits if a["name"] == N_CHARGING), None)
    free = next((a for a in all_hits if a["name"] == N_FREE), None)
    x402_node = next((a for a in all_hits if a["name"] == N_X402), None)
    check("搜到收费 agent", charging is not None,
          f"accepts={charging['accepts'] if charging else '-'}")
    check("搜到免费 agent", free is not None)
    check("搜到 x402 微支付 agent", x402_node is not None,
          f"accepts={x402_node['accepts'] if x402_node else '-'}")
    peer_ready = req("GET", "/v1/discover/peer-ready?skill=ocr-pro")
    check("peer-ready 筛出能成交的（含收费）",
          any(a["name"] == N_CHARGING for a in peer_ready))
    if not (charging and free and x402_node):
        print("（三个节点没找齐：确认 run_a2a_node.py 收费/免费/x402 三个实例都在跑）")
        return 1
    charge_id_agent, free_id = charging["agent_id"], free["agent_id"]
    x402_id = x402_node["agent_id"]

    print("② 免费调用：alice 未建任何账户，直接调免费 agent")
    r = send(free_id, "hello free", "ocr-pro", ALICE, rid=2)
    res = r.get("result") or {}
    xa = (res.get("metadata", {}) or {}).get("x-a2n", {}) or {}
    check("免费调用直接通", res.get("status", {}).get("state") == "completed")
    check("免费不产生账（无 deal 无 charge）",
          xa.get("deal_id") is None and xa.get("charge_id") is None)

    print("③ 收费零准备：直接调收费 agent → 拒，且说清怎么补")
    r = send(charge_id_agent, "hello pay", "ocr-pro", ALICE, rid=3)
    err = r.get("error") or {}
    check("被门禁拦下 (-32001)", err.get("code") == -32001,
          str(err.get("message", ""))[:70])

    print("④ 直付：alice 登记支付宝渠道 → 加上就能调")
    pm = req("POST", "/v1/pay-methods", {"channel": "alipay", "ref": "138****0001"},
             principal=ALICE)
    check("登记支付宝", pm.get("channel") == "alipay", pm.get("pm_id", ""))
    comp = req("GET", f"/v1/pay-methods/compatible?agent_id={charge_id_agent}",
               principal=ALICE)
    check("能力交集出现 direct_pay:alipay",
          "direct_pay:alipay" in comp.get("can_call_with", []),
          f"can_call_with={comp.get('can_call_with')}")
    r = send(charge_id_agent, "hello a2a", "ocr-pro", ALICE, rid=4)
    res = r.get("result") or {}
    xa = (res.get("metadata", {}) or {}).get("x-a2n", {}) or {}
    check("直付调用通过", res.get("status", {}).get("state") == "completed",
          json.dumps((res.get("artifacts") or [{}])[0].get("parts", [{}])[0].get("data", ""),
                     ensure_ascii=False))
    check("结算方式=direct_pay:alipay", xa.get("settle_mode") == "direct_pay:alipay")
    charge_id = xa.get("charge_id")
    check("生成直付凭证", bool(charge_id), f"charge_id={charge_id}")
    pay_task_id = res.get("id")

    print("⑤ 合约核验：授权链/金额/渠道/任务一致性/单价快照")
    d = req("GET", f"/v1/pay-charges/{charge_id}")
    v = d.get("verify", {})
    ch = d.get("charge", {})
    check("核验总开关 ok", v.get("ok") is True,
          f"chain={v.get('chain_ok')} amount={v.get('amount_ok')}"
          f" channel={v.get('channel_ok')} task={v.get('task_ok')} digest={v.get('digest_ok')}")
    check("金额=单价×数量", ch.get("amount_fen") == ch.get("unit_price_fen", 0) * 1,
          f"{ch.get('unit_price_fen')}分 × {ch.get('call_count')} = {ch.get('amount_fen')}分")
    check("授权链三层齐活", all(k in d.get("mandate_chain", {})
                              for k in ("intent", "cart", "payment")))
    check("凭证挂的任务已通过验收", v.get("task_ok") is True)
    check("未回执前只认 AUTHORIZED", ch.get("state") == "AUTHORIZED"
          and v.get("captured") is False, f"state={ch.get('state')}")

    print("⑤b 治理链接上了没：这次调用应已进公证与信誉")
    rep = req("GET", f"/v1/registry/agents/{charge_id_agent}")
    rcpt = req("GET", "/v1/notary/receipts?limit=200")
    kinds = [r.get("event_type") for r in rcpt] if isinstance(rcpt, list) else []
    check("调用进了公证链（task/acceptance 事件）",
          any(k in kinds for k in ("task.submitted", "acceptance.passed")),
          f"近期事件={sorted(set(kinds))[-4:]}")
    check("信誉被更新（不再是初始 0.5）", float(rep.get("reputation", 0.5)) != 0.5,
          f"reputation={rep.get('reputation')}")

    print("⑤c 回执确认：AUTHORIZED → CAPTURED（只有真回执才许改）")
    cb = req("POST", "/v1/custodian/pay-callback",
             {"charge_id": charge_id, "ok": True, "channel_ref": "ch_demo_001"})
    check("回执后 CAPTURED", cb.get("state") == "CAPTURED", str(cb.get("state")))
    d2 = req("GET", f"/v1/pay-charges/{charge_id}")
    check("核验仍全绿", d2.get("verify", {}).get("ok") is True
          and d2.get("verify", {}).get("captured") is True)

    print("⑥ 对等账户：dave 建账户+配对 → 同一个 agent，另一种结算")
    acc = req("POST", "/v1/party-accounts", {"label": "dave-对公"}, principal=DAVE)
    link = req("POST", "/v1/peers",
               {"account_id": acc["account_id"], "agent_id": charge_id_agent,
                "peer_ref": "bob@dave-对公", "auto_accept": True}, principal=DAVE)
    check("配对 ACTIVE", link.get("state") == "ACTIVE", link.get("link_id", ""))
    r = send(charge_id_agent, "hello ledger", "ocr-pro", DAVE, rid=6)
    res = r.get("result") or {}
    xa = (res.get("metadata", {}) or {}).get("x-a2n", {}) or {}
    check("对等调用通过", res.get("status", {}).get("state") == "completed")
    check("结算方式=peer_account 且有 deal",
          xa.get("settle_mode") == "peer_account" and bool(xa.get("deal_id")),
          f"deal_id={xa.get('deal_id')}")

    print("⑦ tasks/get 复查（直付那单）")
    again = req("POST", f"/a2a/{charge_id_agent}", {
        "jsonrpc": "2.0", "id": 7, "method": "tasks/get",
        "params": {"id": pay_task_id}}, principal=ALICE)
    check("任务可查", (again.get("result") or {}).get("status", {}).get("state") == "completed")
    check("任务挂上了记账凭据",
          ((again.get("result") or {}).get("metadata", {}).get("x-a2n", {}) or {}).get("charge_id") == charge_id)

    print("⑧ x402 微支付：请求即付，无需预先建立任何关系")
    r = send(x402_id, "hello x402", "ocr-pro", ALICE, rid=8)
    err = r.get("error") or {}
    check("无凭证 → 402 挑战", err.get("code") == -32002 and r.get("__status") == 402,
          f"http={r.get('__status')} code={err.get('code')}")
    req_obj = (err.get("data") or {})
    acc = (req_obj.get("accepts") or [{}])[0]
    check("挑战体符合 x402 形状",
          req_obj.get("x402Version") == 1 and acc.get("scheme") == "exact",
          f"maxAmountRequired={acc.get('maxAmountRequired')} network={acc.get('network')}")

    pay = {"x402Version": 1, "scheme": "exact", "network": acc.get("network"),
           "payload": {"amount": int(acc.get("maxAmountRequired") or 1),
                       "signature": "0xdemo-signature"}}
    r = send(x402_id, "hello x402", "ocr-pro", ALICE, rid=9,
             payment=encode_payment(pay))
    res = r.get("result") or {}
    xa = (res.get("metadata", {}) or {}).get("x-a2n", {}) or {}
    check("带凭证重试 → 通过", res.get("status", {}).get("state") == "completed",
          json.dumps((res.get("artifacts") or [{}])[0].get("parts", [{}])[0].get("data", ""),
                     ensure_ascii=False))
    x402_charge = xa.get("charge_id")
    check("x402 结算方式", xa.get("source") == "a2a" and bool(x402_charge),
          f"charge={x402_charge}")
    if x402_charge:
        dx = req("GET", f"/v1/pay-charges/{x402_charge}")
        check("x402 即付已 CAPTURED 且核验通过",
              dx.get("charge", {}).get("state") == "CAPTURED"
              and dx.get("verify", {}).get("ok") is True,
              f"state={dx.get('charge', {}).get('state')} amount={dx.get('charge', {}).get('amount_fen')}分")

    if fails:
        print(f"\n失败 {len(fails)} 项：{fails}")
        return 1
    print("\n双通道验证通过：发现开放 → 免费直调 → 收费先验支付 → 登记支付宝即调（直付凭证+授权链）"
          "→ 配对走对等账户（deal 对账）→ 合约可核验。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
