"""计量签名的**接线**：能力算对了，还得有人在真实链路上把它签出来。

背景是一条已经踩过的坑：`sign_metering` / `verify_metering` 都写好了、
单测也过了，但**没有任何运行路径真的产生过签名** —— 活库里的计量栏永远是
"未签名"。这类"算对了却没人看得到"的死代码，比没写更危险：它让人以为
证据面已经立起来了。

所以这组测试守的不是算法（算法在 test_quality 里已经守过），而是**接线**：

  · inline 交付（v1 主链路）：平台代节点提交，节点唯一的机会是在**转发边界**
    上就地连署 —— TunnelClient 必须把签名随回包上行；
  · 推送交付：节点自己提交，Node 必须签**它即将上报的那份计量**；
  · 网关：拿到的 attest 必须原样交给 tasks.submit，最后落进 usage_reports
    （`attested=1` + "验签通过"）；
  · 上行路由：`tunnel/up` 是手写的形状白名单，最容易把 attest **静默吃掉** ——
    真被吃过一次，活库上表现为"永远未签名"，而单测照样全绿（它们不经过路由）；
  · 反向：没给身份就如实"未签名"，两边口径漂移就进争议 —— 不许悄悄放过。
"""
from __future__ import annotations

import threading

from a2n_gateway import invoke
from a2n_kernel.hashing import new_id
from a2n_p2p import Identity, pub_b64, sign_metering, verify_metering
from a2n_registry import registry
from a2n_sdk import Client, Node
from a2n_sdk.transport import TunnelClient
from a2n_store import conn
from a2n_transport import hub

NODE = "ag_wiring_node"


class Recording(Client):
    """把 _req 拦下来，什么都不发出去。"""

    def __init__(self) -> None:  # noqa: D107 - 故意不走 Client.__init__
        self.base = "http://127.0.0.1:8000"
        self.principal = "acct:tester"
        self.node_id = NODE
        self.calls: list[tuple] = []

    def _req(self, method, path, body=None, headers=None):
        self.calls.append((method, path, body, headers))
        return {"ok": True}


def _uid(tag: str) -> str:
    return f"acct:{tag}-{new_id('')[2:8]}"


def _usage_row(task_id: str) -> dict:
    return dict(conn().execute(
        "SELECT * FROM usage_reports WHERE task_id=? ORDER BY rowid DESC LIMIT 1",
        (task_id,)).fetchone())


# ---------------------------------------------------------------- inline 交付

def test_forward_boundary_countersigns_the_metering_contract():
    """inline 交付：转发边界必须就地连署，并把签名随回包上行。

    local_base=None → 不真起本地 HTTP 服务（本机代理可能拦回环，测试不该依赖网络），
    但 `tunnel/up` 的上行体照样构造 —— 要验的正是"签名有没有进那个上行体"。
    """
    ident = Identity.generate()
    c = Recording()
    tc = TunnelClient(c, on_task=lambda m: None, local_base=None,
                      attest_fn=lambda tid, nid, dims: sign_metering(
                          ident, task_id=tid, node_id=nid, dims=dims))
    tc._handle_forward({"req_id": "req_1", "method": "POST", "path": "/invoke",
                        "body": {"skill": "ocr-pro"},
                        "task_id": "t_wiring_1", "dims": {"call_count": 1}})

    method, path, body, _ = c.calls[-1]
    assert (method, path) == ("POST", f"/v1/nodes/{NODE}/tunnel/up")
    assert "attest" in body, "转发边界没连署 —— 计量签名就成了没人看得到的死代码"
    ok, why = verify_metering(body["attest"], expect_task_id="t_wiring_1",
                              expect_node_id=NODE, expect_dims={"call_count": 1})
    assert ok, why


def test_forward_boundary_without_identity_stays_honest():
    """没接身份就不许编：上行体里没有 attest，而不是塞一个假的。"""
    c = Recording()
    tc = TunnelClient(c, on_task=lambda m: None, local_base=None)
    tc._handle_forward({"req_id": "req_2", "method": "POST", "path": "/invoke",
                        "body": {"skill": "ocr-pro"},
                        "task_id": "t_wiring_2", "dims": {"call_count": 1}})
    assert "attest" not in c.calls[-1][2]


def test_forward_boundary_without_task_id_does_not_sign():
    """没有任务号就没什么可绑的 —— 签一份绑不上单的计量等于没签。"""
    ident = Identity.generate()
    c = Recording()
    tc = TunnelClient(c, on_task=lambda m: None, local_base=None,
                      attest_fn=lambda tid, nid, dims: sign_metering(
                          ident, task_id=tid, node_id=nid, dims=dims))
    tc._handle_forward({"req_id": "req_3", "method": "POST", "path": "/invoke",
                        "body": {"skill": "ocr-pro"}, "dims": {"call_count": 1}})
    assert "attest" not in c.calls[-1][2]


# ---------------------------------------------------------------- 推送交付

def test_push_delivery_signs_the_usage_it_reports():
    """推送交付：节点自己提交，就该签**它即将上报的那份计量**（不是别人给的数）。"""
    ident = Identity.generate()
    node = Node({"name": "n", "version": "1.0.0", "skills": [], "x-a2n": {}},
                {"ocr-pro": lambda payload: {"text": "OK", "pages": 1}},
                principal="acct:tester",
                attest_fn=lambda tid, nid, dims: sign_metering(
                    ident, task_id=tid, node_id=nid, dims=dims))
    node.client = Recording()
    node._handle({"id": "t_wiring_3", "skill_id": "ocr-pro", "payload": {"text": "hi"}})

    call = [x for x in node.client.calls if x[1] == "/v1/tasks/t_wiring_3/result"][-1]
    usage = call[2]["usage"]
    assert "attest" in usage
    dims = {k: v for k, v in usage.items() if k != "attest"}
    ok, why = verify_metering(usage["attest"], expect_task_id="t_wiring_3",
                              expect_node_id=NODE, expect_dims=dims)
    assert ok, why
    assert set(dims) >= {"call_count", "wall_time_ms"}, f"自报口径异常：{dims}"


# ---------------------------------------------------------------- 网关

def test_forward_and_reply_roundtrip_carries_task_id_dims_and_attest():
    """转发枢纽这一层：下行要带 task_id/dims，上行要能把 attest 带回来。

    两条消息都是 dict，谁少拷一个字段都不会报错 —— 只会让签名在上行路上消失。
    """
    tid = hub.open(NODE, {})["tunnel_id"]
    res: dict = {}

    def _call() -> None:
        res["out"] = hub.forward(NODE, "POST", "/invoke", {"skill": "ocr-pro"},
                                 caller="acct:x", task_id="t_rt", dims={"call_count": 1})

    t = threading.Thread(target=_call)
    t.start()
    msg = None
    for _ in range(200):                       # 等下行消息上队
        m = hub.poll(tid, wait=0.05)
        if m and m.get("type") == "forward":
            msg = m
            break
    assert msg is not None, "下行没收到转发请求"
    assert msg["task_id"] == "t_rt" and msg["dims"] == {"call_count": 1}, msg

    att = {"did": "d", "pub": "p", "sig": "s", "payload": {"task_id": "t_rt"}}
    assert hub.reply(NODE, msg["req_id"], {"status": 200, "body": {"ok": 1}, "attest": att})
    t.join(5)
    assert res["out"].get("attest") == att, "上行把签名吃掉了"


def test_tunnel_up_route_does_not_swallow_the_attest(monkeypatch):
    """★ 上行路由的形状白名单最容易静默吃掉 attest。

    真实踩过：`TunnelUpIn` 只声明了 req_id/status/body，路由又手写构造
    `{"status":…, "body":…}` —— 节点签好的计量到这一层就没了，
    活库上表现为"永远未签名"，而**所有单测照样全绿**（它们不经过路由）。
    """
    from a2n_server.routers import transport as tr

    class FakeHub:
        def __init__(self) -> None:
            self.seen: dict | None = None

        def reply(self, node_id, req_id, payload):  # noqa: ANN001
            self.seen = payload
            return True

    fake = FakeHub()
    monkeypatch.setattr(tr, "hub", fake)
    att = {"did": "d", "pub": "p", "sig": "s", "payload": {"task_id": "t_up"}}
    tr.tunnel_up(NODE, tr.TunnelUpIn(req_id="r1", status=200, body={"ok": 1}, attest=att))
    assert fake.seen is not None
    assert fake.seen["attest"] == att, "上行路由把节点连署的计量吃掉了"
    assert fake.seen["body"] == {"ok": 1}


def _card(name: str, skill: str, ident: Identity, *, free: bool = True) -> dict:
    ext: dict = {
        "deployment": {"region": "cn-east-2"},
        "metering": {"dimensions": [{"key": "call_count", "verifiable": True}]},
        # 卡上自证：平台按**卡声明的公钥**认签名的钥匙，所以必须与签名身份一致
        "sovereign": {"did": ident.did, "pub": pub_b64(ident.pub_raw)},
    }
    card: dict = {"name": name, "version": "1.0.0", "url": None,
                  "skills": [{"id": skill, "name": skill}], "x-a2n": ext}
    if free:
        card["accepts"] = []
    return card


def _node_stub(monkeypatch, ident: Identity, *, dims_override: dict | None = None):
    """模拟节点侧：收到 task_id + 计费口径就地连署，随回包带回。

    刻意**从转发参数里取** task_id/dims（而不是写死）—— 这既复刻了节点的真实处境
    （它只被告诉这两样），也顺便钉住"网关确实把它们下发下去了"。
    """
    seen: dict = {}

    def _fwd(agent_id, method, path, body, caller=None, task_id=None, dims=None):
        seen.update({"agent_id": agent_id, "task_id": task_id, "dims": dims})
        return {"status": 200, "body": {"body": {"answer": "42"}},
                "attest": sign_metering(ident, task_id=task_id, node_id=agent_id,
                                        dims=dims_override if dims_override else dims)}

    monkeypatch.setattr(hub, "forward", _fwd)
    return seen


def test_gateway_carries_node_attest_into_usage_reports(monkeypatch):
    """网关必须把节点连署的计量交给提交/入账 —— 否则签了也白签。"""
    ident = Identity.generate()
    skill = f"w-{new_id('')[2:8]}"
    aid = registry.register(_uid("w-prov"), _card("wiring", skill, ident))["agent_id"]
    seen = _node_stub(monkeypatch, ident)

    out = invoke(agent_id=aid, principal=_uid("w-user"), skill=skill, payload={"x": 1})
    assert out.ok, out.to_dict()
    # 下发的是"这一单 + 计费口径"：少了它，节点就不知道自己在为哪一单干活
    assert seen["task_id"] == out.task_id
    assert seen["dims"] == {"call_count": 1}
    assert seen["agent_id"] == aid

    u = _usage_row(out.task_id)
    assert u["attested"] == 1 and u["attest_reason"] == "验签通过"
    assert u["status"] == "reconciled"
    assert u["signature"] and u["signature"] != "mock-signature"


def test_gateway_flags_drifted_metering_instead_of_waving_it_through(monkeypatch):
    """反向：节点签的口径与入账口径漂移 → 进争议，绝不悄悄放过。

    "网关下发的 dims"与"入账用的 dims"必须是同一份；谁哪天在一边改了个常量，
    这条测试就会由绿转红 —— 而不是让每一次真实调用都静默进争议。
    """
    ident = Identity.generate()
    skill = f"w-{new_id('')[2:8]}"
    aid = registry.register(_uid("w2-prov"), _card("wiring-drift", skill, ident))["agent_id"]
    _node_stub(monkeypatch, ident, dims_override={"call_count": 999})

    out = invoke(agent_id=aid, principal=_uid("w2-user"), skill=skill, payload={"x": 1})
    u = _usage_row(out.task_id)
    assert u["attested"] == 0 and u["status"] == "disputed"
    assert "不一致" in u["attest_reason"]
