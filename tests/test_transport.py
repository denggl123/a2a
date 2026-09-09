"""传输阶梯：p2p 穿透候选、反向长连接、中继转发。

核心断言：
1. 隧道在线 = 可达（无需公网 IP、无需心跳也能判定）
2. 中继转发：平台公网入口 → 隧道 → 节点本地服务 → 回包原路返回
3. 隧道断开 → 自动降级，不可派单（预算不能冻在送不进去的单上）
4. p2p 打洞只做候选枚举，永不被自动选中（证据路径必须走平台）
5. 协商结果 = 候选清单 + 最优通道（ICE 思路）
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from a2n_dispatch import discovery
from a2n_registry import registry
from a2n_kernel.hashing import new_id
from a2n_transport import hub, negotiate

from .test_flow import demo_card


def _local_http() -> tuple[ThreadingHTTPServer, int]:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def do_POST(self) -> None:
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n).decode() if n else "{}"
            out = json.dumps({"path": self.path, "got": json.loads(body),
                              "served_by": "node-local-9101"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _node_with_tunnel() -> str:
    s = new_id("")[2:]
    agent = registry.register(f"acct:tn{s}", demo_card("tn-" + s))
    aid = agent["agent_id"]
    hub.open(aid, {"local_base": "http://127.0.0.1:9101"})
    return aid


def test_tunnel_implies_reachable():
    aid = _node_with_tunnel()
    # 隧道刚开但从未"轮询"？hub.alive 要求 last_poll 新鲜 —— 开隧道即视为活性起点
    ok, why = discovery.assignable(aid)
    assert ok, why
    n = negotiate(registry.get(aid))
    assert n["chosen"] in ("tunnel", "relay"), n


def test_relay_forward_roundtrip():
    aid = _node_with_tunnel()
    _, port = _local_http()

    # 模拟节点侧隧道客户端：下行取转发请求 → 打本地服务 → 上行回包
    def node_side():
        while True:
            msg = hub.poll(hub.status(aid)["tunnel_id"], wait=2.0)
            if not msg or msg.get("type") == "tunnel.closed":
                return
            if msg.get("type") == "forward":
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}{msg['path']}",
                    data=json.dumps(msg.get("body")).encode(), method="POST")
                req.add_header("Content-Type", "application/json")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    out = json.loads(resp.read().decode())
                hub.reply(aid, msg["req_id"], {"status": 200, "body": out})

    threading.Thread(target=node_side, daemon=True).start()

    from a2n_transport import FORWARD_TIMEOUT_S
    payload = hub.forward(aid, "POST", "/echo", {"hello": "world"})
    assert payload.get("status") == 200, payload
    body = payload["body"]
    assert body["served_by"] == "node-local-9101"
    assert body["got"] == {"hello": "world"}
    assert FORWARD_TIMEOUT_S >= 5


def test_no_tunnel_falls_back():
    s = new_id("")[2:]
    agent = registry.register(f"acct:nt{s}", demo_card("nt-" + s))
    registry.heartbeat(agent["agent_id"])
    n = negotiate(registry.get(agent["agent_id"]))
    assert n["chosen"] == "pull", n
    ok, _ = discovery.assignable(agent["agent_id"])
    assert ok
    # 隧道死了 → 声明为 tunnel 模式的节点必须不可派
    hub.open(agent["agent_id"], {})
    import sqlite3
    from a2n_store import conn
    conn().execute("UPDATE agents SET connection=? WHERE agent_id=?",
                   (json.dumps({"mode": "tunnel"}), agent["agent_id"]))
    conn().commit()
    # 伪造 stale：把 last_poll 拨回过去
    t = hub._pick(agent["agent_id"])
    t.last_poll = time.time() - 3600
    ok, why = discovery.assignable(agent["agent_id"])
    assert not ok and "反向通道" in why


def test_holepunch_never_auto_chosen():
    """p2p 打洞是候选不是依赖：即使有反射候选也不能被自动选中。"""
    s = new_id("")[2:]
    agent = registry.register(f"acct:hp{s}", demo_card("hp-" + s))
    registry.heartbeat(agent["agent_id"],
                       {"mode": "pull", "candidates": ["203.0.113.7:51820"]})
    n = negotiate(registry.get(agent["agent_id"]))
    hp = next(c for c in n["candidates"] if c["transport"] == "holepunch")
    assert hp["usable"] is False and "候选" in hp["detail"]
    assert n["chosen"] != "holepunch"


def test_tunnel_poll_piggybacks_heartbeat():
    aid = _node_with_tunnel()
    tid = hub.status(aid)["tunnel_id"]
    hub.poll(tid, wait=0.1)
    from a2n_registry.reachability import reachable
    ok, why = reachable(registry.get(aid))
    assert ok, why
