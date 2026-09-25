"""A2A 协议层端到端冒烟（发现开放 + 直付/对等双结算通道 + 合约核验）。

链路（一次跑完，全部走真实 HTTP）：
  ① 开放发现：零准备也能搜到收费与免费 agent；peer-ready 只是可选便利筛选
  ② 免费调用：使用方零准备直接调免费 agent → completed，不产生任何账
  ③ 收费零准备：直接调收费 agent → -32001，错误信息列全"对方收什么、怎么补"
  ④ 直付：使用方登记支付宝（POST /v1/pay-methods）→ 直接就能调 → CAPTURED 凭证
  ⑤ 合约核验：GET /v1/pay-charges/{id} 重验授权链/金额/任务一致性/单价漂移
  ⑥ 对等账户：dave 建账户+配对 → 调同一个收费 agent → deal RECONCILED
  ⑦ tasks/get 复查

使用方 = **本机节点的 did**（一个节点一个身份，2026-09-20）—— 与控制台开箱身份是
同一个主体，所以这些调用会在控制台的「调用记录 / 流水」里如实出现。另造一个
`acct:alice` 当使用方会让那两页变空，而本脚本全绿（假绿，真踩过）。

前置：平台(A2N_DB=data/e2e_a2a.db) + 本机节点(四张卡，9102-9105) 都在跑。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("A2N_BASE", "http://127.0.0.1:18787")
# 使用方主体 = **本机节点的 did**（一个节点一个身份，2026-09-20）。
# 它就是控制台开箱的那个身份 —— 一个身份本来就既买又卖，所以冒烟里的"使用方"
# **不能**是另造的一个账号：那样控制台上的「调用记录」和「流水列表」会双双空白，
# 而脚本自己照样全绿（2026-09-20 真踩：③ 报"调用记录 0 行 / 流水 0 行"，
# 查一圈发现功能全好，是使用方与开箱身份不是同一个人）。
# 具体值在 main() 里从本机节点自己那张 OCR 卡的**公开自证**里读出来（见 seller_principal）。
BUYER = ""
DAVE = "acct:dave"          # 另一端：证明"跨主体调用 / 对等结算"真的成立

# demo 四节点的展示名（与 scripts/run_a2a_node.py 的 PRESETS 对应；
# 按角色改名时这里要一起改，否则冒烟找不到节点）
N_CHARGING = "OCR 识别 · 专业版"
N_FREE = "OCR 识别 · 公益版"
N_X402 = "OCR 识别 · 极速版"
# alice（= 控制台开箱的那个主体）上架的行业案例之一，见 run_market_demo_agents.py。
# 案例现在跑在 docker 容器里（docker/market_node.py），**最后一跳故意不通** ——
# 所以 ⑨ 拿它验的是"别人搜得到、调得到、调不通时如实说"这一整套（见 ⑨）。
N_MINE = "短视频口播稿"
# 被调节点"转发给自己那台 agent"失败时的措辞（docker/market_node.py::_agent_behind）。
# **故意钉死**：⑨ 的整个意义就是"失败原因出自那次真实尝试"。改了那句措辞就必须同步这里，
# 否则这条断言会退化成一句空话（换文案它照样绿 —— 那就等于没验）。
FORWARD_FAIL_MARK = "转发到上游 agent"

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


def clear_direct_pay(principal: str) -> list[str]:
    """注销该主体名下所有还生效的直付渠道，返回被注销的 pm_id 列表。

    ③ 验的是**门禁本身**（"零准备就调收费 → 拦下"），不是库里恰好没存量。
    演示库是**保留**的（重建/重启不清库），所以第二次跑冒烟时上一轮登记的
    支付宝还在 —— 不清掉，③ 就会假红（2026-09-25 真踩：保留数据的重建后
    ③ 报"没被拦下"，查一圈发现门禁完好，是存量没清）。清完 ④ 会重新登记，
    净效果与冷启一致。（平台没有全局清库入口，这是最贴近"零准备"的做法。）
    """
    closed: list[str] = []
    for pm in req("GET", "/v1/pay-methods", principal=principal) or []:
        if pm.get("status") == "ACTIVE":
            req("POST", f"/v1/pay-methods/{pm['pm_id']}/close", principal=principal)
            closed.append(pm["pm_id"])
    return closed


def seller_principal(agent_id: str) -> str | None:
    """从**公开的卡**里读出卖家主体。

    一个节点一个身份（2026-09-20）之后，"谁在卖"只有一个答案：节点密钥
    派生的 did。但它在卡上的位置取决于卡的形状：
      - 平台投影卡：原 sovereign（带签名）不能跨投影携带（带原 sig 就不再是
        原卡），来源折进 `x-a2n.origin.did`；
      - 节点直出卡（自持/P2P/owner 原卡）：仍在 `x-a2n.sovereign.did`。
    两处都是同一个节点身份，按形状取，绝不退回猜账号名。
    """
    a = req("GET", f"/v1/registry/agents/{agent_id}") or {}
    try:
        card = json.loads(a.get("card_json") or "{}")
    except ValueError:
        return None
    ext = card.get("x-a2n") or {}
    return ((ext.get("origin") or {}).get("did")
            or (ext.get("sovereign") or {}).get("did"))


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

    # 使用方 = 本机节点自己的 did（从它那张收费卡的公开自证里读）。读不到就**当场失败**，
    # 绝不退回 `acct:alice` 那种假账号：退回去，控制台的「调用记录 / 流水」会重新变空，
    # 而这一段会照旧打印"全绿" —— 那正是 2026-09-20 那次假绿。
    global BUYER
    BUYER = seller_principal(charge_id_agent) or ""
    check("本机节点身份读得到（= 控制台开箱的那个主体，一个节点一个身份）",
          BUYER.startswith("did:"), f"buyer={BUYER or '读不到'}")
    if not BUYER:
        print("（读不到本机节点身份：确认 sim_start.sh 已拉起本机节点、且它四张卡都上架成功）")
        return 1

    print("② 免费调用：本机节点未建任何账户，直接调免费 agent")
    r = send(free_id, "hello free", "ocr-pro", BUYER, rid=2)
    res = r.get("result") or {}
    xa = (res.get("metadata", {}) or {}).get("x-a2n", {}) or {}
    check("免费调用直接通", res.get("status", {}).get("state") == "completed")
    check("免费不产生账（无 deal 无 charge）",
          xa.get("deal_id") is None and xa.get("charge_id") is None)

    print("③ 收费零准备：直接调收费 agent → 拒，且说清怎么补")
    closed = clear_direct_pay(BUYER)
    if closed:
        print(f"（使用方名下有存量直付渠道 {closed}，先注销——否则这条验的不是门禁）")
    r = send(charge_id_agent, "hello pay", "ocr-pro", BUYER, rid=3)
    err = r.get("error") or {}
    check("被门禁拦下 (-32001)", err.get("code") == -32001,
          str(err.get("message", ""))[:70])

    print("④ 直付：本机节点登记支付宝渠道 → 加上就能调")
    pm = req("POST", "/v1/pay-methods", {"channel": "alipay", "ref": "138****0001"},
             principal=BUYER)
    check("登记支付宝", pm.get("channel") == "alipay", pm.get("pm_id", ""))
    comp = req("GET", f"/v1/pay-methods/compatible?agent_id={charge_id_agent}",
               principal=BUYER)
    check("能力交集出现 direct_pay:alipay",
          "direct_pay:alipay" in comp.get("can_call_with", []),
          f"can_call_with={comp.get('can_call_with')}")
    r = send(charge_id_agent, "hello a2a", "ocr-pro", BUYER, rid=4)
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
    # peer_ref = "对端账户标识"（agent 侧给的收款账户/对账编号），自由文本。
    # 别再写 "bob@…"：那是一个已经被删掉的人造账号（2026-09-20 起一节点一身份），
    # 留着它等于让人以为还有第二个主体在收钱。
    link = req("POST", "/v1/peers",
               {"account_id": acc["account_id"], "agent_id": charge_id_agent,
                "peer_ref": "本机节点@dave-对公", "auto_accept": True}, principal=DAVE)
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
        "params": {"id": pay_task_id}}, principal=BUYER)
    check("任务可查", (again.get("result") or {}).get("status", {}).get("state") == "completed")
    check("任务挂上了记账凭据",
          ((again.get("result") or {}).get("metadata", {}).get("x-a2n", {}) or {}).get("charge_id") == charge_id)

    print("⑧ x402 微支付：请求即付，无需预先建立任何关系")
    r = send(x402_id, "hello x402", "ocr-pro", BUYER, rid=8)
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
    r = send(x402_id, "hello x402", "ocr-pro", BUYER, rid=9,
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

    print("⑨ 别人调我的单：另一主体调**容器节点**上架的案例（案例跑在 docker 容器里）")
    # 三个容器 = 公网上的另外三台机器（docker 模拟），各一个身份、各上架两张卡。
    # 控制台 → 平台 → relay 入口 → 反向隧道 → 容器内本地服务 这段是真生产链路
    # （注册/心跳/地址投影/门禁/拨号一处不少）；容器里那**最后一跳真的连不上**
    # （去连一台不存在的上游 agent）。所以这一节验的不是"调得通"，而是
    # **断在哪一跳、有没有如实说**：卡是真的、搜得着、对方节点是活的、门禁也放行
    # （免费档），然后断在**它转发给自己那台 agent** 的那一步，失败原因是那次
    # 真实尝试的结果 —— 不是一句 "upstream 500" 的通用噪声（那种话把
    # "参数错 / 节点坏了 / 按设计就不通"三种情况糊成一种，等于没给证据）。
    hits = req("POST", "/v1/discovery/query", {"require": {"skill": "video-script"}})
    hits = hits if isinstance(hits, list) else []
    mine = next((a for a in hits if a["name"] == N_MINE), None)
    check("容器节点上架的案例被别人搜得到", mine is not None,
          f"video-script 命中 {len(hits)} 条")
    if mine:
        check("发现路把它标成可达、卡自证通过（对方节点是活的，不是掉线）",
              mine.get("reachable") is True and mine.get("selfproof") == "signed",
              f"reachable={mine.get('reachable')} selfproof={mine.get('selfproof')}")
        r = send(mine["agent_id"], "季末上新\n三个卖点", "video-script", DAVE, rid=10)
        res = r.get("result") or {}
        state = (res.get("status") or {}).get("state")
        check("转发那一跳不通：任务如实失败（不是静默成功、也不是空壳结果）",
              state == "failed",
              f"state={state} artifacts={len(res.get('artifacts') or [])}")
        reason = ((res.get("status") or {}).get("error") or {}).get("error") or ""
        check("失败原因出自被调节点的真实尝试，不是通用噪声",
              FORWARD_FAIL_MARK in reason and "upstream" not in reason.lower(),
              f"reason={reason[:90]}")
        # 供给方视角：主体 = **被调节点自己的 did**（一个节点一个身份）。
        seller = seller_principal(mine["agent_id"])
        check("卖家主体从卡里读得到（= 节点自己的 did，不是另造的账号）",
              bool(seller) and seller.startswith("did:"), f"seller={seller}")
        got = (req("GET", "/v1/console/provided-calls?limit=50", principal=seller) or []
               if seller else [])
        check("供给方视角看得到这一笔（失败也照记，不许只记成功的）",
              any(c.get("requester_id") == DAVE and c.get("agent_name") == N_MINE
                  for c in got), f"共 {len(got)} 笔")

    if fails:
        print(f"\n失败 {len(fails)} 项：{fails}")
        return 1
    print("\n双通道验证通过：发现开放 → 免费直调 → 收费先验支付 → 登记支付宝即调（直付凭证+授权链）"
          "→ 配对走对等账户（deal 对账）→ 合约可核验。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
