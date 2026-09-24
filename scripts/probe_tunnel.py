"""黑盒复现：模拟节点经 HTTP 建隧道 → 平台 relay 调用 → 隧道回包。"""
import json
import threading
import time
import urllib.request

BASE = "http://127.0.0.1:18787"


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    r.add_header("X-Principal", "acct:probe")
    with urllib.request.urlopen(r, timeout=35) as resp:
        return json.loads(resp.read().decode() or "null")


# 1. 注册一个节点
card = {"name": "probe-node", "url": None,
        "skills": [{"id": "probe"}],
        "x-a2n": {"compute": {}, "sla": {}, "price_hint": {"probe": {"amount": 1}}}}
a = req("POST", "/v1/registry/agents", {"card": card})
aid = a["agent_id"]
print("agent", aid)

# 2. 建隧道
t = req("POST", f"/v1/nodes/{aid}/tunnel", {"mode": "relay", "meta": {}})
tid = t["tunnel_id"]
print("tunnel", tid)

got = {}


def poller():
    print("  [poller] started", flush=True)
    n = 0
    while not got.get("stop"):
        try:
            t0 = time.time()
            m = req("GET", f"/v1/nodes/{aid}/tunnel/next?tid={tid}&wait=3")
            print(f"  [poll#{n}] {round(time.time()-t0,1)}s {json.dumps(m, ensure_ascii=False)[:120]}",
                  flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  [poll#{n}] 异常 {type(e).__name__}: {e}", flush=True)
            return
        n += 1
        if m and m.get("type") == "forward":
            got["forward"] = m
            req("POST", f"/v1/nodes/{aid}/tunnel/up",
                {"req_id": m["req_id"], "status": 200, "body": {"pong": True, "echo": m["body"]}})
        if m and m.get("type") == "tunnel.closed":
            print("  [poll] 隧道被服务端关闭！", flush=True)
            return
        # idle / closed 继续轮


th = threading.Thread(target=poller, daemon=True)
th.start()
time.sleep(1)

# 3. 平台 relay 调用
t0 = time.time()
r = req("POST", f"/v1/relay/{aid}/echo", {"hello": "world"})
print("relay", round(time.time() - t0, 2), "s ->", r)
got["stop"] = True
