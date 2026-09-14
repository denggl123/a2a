"""传输阶梯：把 p2p 穿透、反向长连接、中继转发收进同一套协商规则。

先摆清一个事实，它决定三条通道各自的用途：

**平台有公网入口，所以"节点↔平台"永远只需要出站，不需要打洞。**
打洞（ICE/STUN）只服务"两端都在 NAT 后"的场景——例如使用方想 P2P 直连
节点省中继流量。但 A2N 的计量、验收、仲裁都要求证据路径可审计，所以
这里有三条不可妥协的边界：

1. **打洞只做候选，永不做依赖。** 对称 NAT / CGNAT 下成功率低，且
   "两端都在 NAT 后"在 A2N v1 的调用拓扑里根本不出现（平台有公网）。
2. **数据可以走直连，证据必须走平台。** 哪怕将来 P2P 打通了，
   结果 hash、计量、分账证据仍须回平台刻章——否则验收体系失明。
3. **中继是唯一 100% 保证连通的通道。** 业界统计约 8~20% 的连接
   最终靠 TURN；我们把它做成隧道之上的公网入口，而不是最后的惊喜。

阶梯（按优先级协商，自动降级）：
    direct    节点真有公网 URL，入站探测通过        → 延迟最低
    holepunch STUN 候选 + 平台仲裁打洞（v1 仅枚举）  → 实验性
    tunnel    反向长连接：节点出站挂住，平台复用下发  → 家宽适用，实时
    relay     中继转发：平台公网入口 → 经隧道 → 节点本地服务
    pull      长轮询兜底，永远可用
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any

TUNNEL_FRESH_S = 60.0        # 隧道心跳窗口：最后一次下行轮询距今
FORWARD_TIMEOUT_S = 15.0     # 中继转发等待节点回包的上限


class Tunnel:
    def __init__(self, tunnel_id: str, agent_id: str, meta: dict) -> None:
        self.tunnel_id = tunnel_id
        self.agent_id = agent_id
        self.meta = meta or {}
        self.opened_at = time.time()
        self.last_poll = time.time()
        self.downlink: list[dict] = []
        self.responses: dict[str, tuple[threading.Event, dict | None]] = {}
        self.lock = threading.Lock()


class TunnelHub:
    """反向长连接注册表 + 中继转发枢纽（进程内实现，生产换 Redis pub/sub）。"""

    def __init__(self) -> None:
        self._tunnels: dict[str, Tunnel] = {}
        self._cond = threading.Condition()

    # ---- 反向长连接 ----
    def open(self, agent_id: str, meta: dict | None = None) -> dict:
        tid = f"tn_{uuid.uuid4().hex[:12]}"
        with self._cond:
            # 同一节点的旧隧道作废：一条隧道一份计费责任，避免重复投递
            self._tunnels = {k: v for k, v in self._tunnels.items() if v.agent_id != agent_id}
            self._tunnels[tid] = Tunnel(tid, agent_id, meta or {})
            self._cond.notify_all()
        return {"tunnel_id": tid, "agent_id": agent_id}

    def poll(self, tunnel_id: str, wait: float = 25.0) -> dict | None:
        """下行长轮询：挂住等下投消息。顺带刷新隧道活性。"""
        deadline = time.time() + max(0.0, min(wait, 30.0))
        with self._cond:
            while True:
                t = self._tunnels.get(tunnel_id)
                if not t:
                    return {"type": "tunnel.closed"}
                t.last_poll = time.time()
                if t.downlink:
                    return t.downlink.pop(0)
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self._cond.wait(timeout=min(remaining, 0.5))

    def push(self, agent_id: str, msg: dict) -> bool:
        with self._cond:
            t = self._pick(agent_id)
            if not t:
                return False
            t.downlink.append(msg)
            self._cond.notify_all()
            return True

    def alive(self, agent_id: str) -> bool:
        with self._cond:
            t = self._pick(agent_id)
            return bool(t and (time.time() - t.last_poll) < TUNNEL_FRESH_S)

    def status(self, agent_id: str) -> dict | None:
        with self._cond:
            t = self._pick(agent_id)
            if not t:
                return None
            return {"tunnel_id": t.tunnel_id, "age_s": int(time.time() - t.opened_at),
                    "idle_s": round(time.time() - t.last_poll, 1), "queued": len(t.downlink),
                    "meta": t.meta}

    # ---- 中继转发 ----
    def forward(self, agent_id: str, method: str, path: str, body: Any,
                caller: str | None = None, task_id: str | None = None,
                dims: dict | None = None) -> dict:
        """平台公网入口 → 经隧道 → 节点本地服务。阻塞等回包。

        caller 是凭据验过之后的调用方身份，随请求透传给节点——
        节点侧因此知道"这次调用是谁发起的"，也为其本地二次校验留了口子。

        task_id / dims 是**这一单的坐标与计费口径**：不带下去，节点就不知道
        自己在为哪一单干活，也就无法在自己的交付边界上给计量连署——
        于是"计量签名"会变成一条算了却没人看得到的死代码。
        """
        req_id = f"fwd_{uuid.uuid4().hex[:12]}"
        with self._cond:
            t = self._pick(agent_id)
            if not t:
                return {"error": "no_tunnel", "message": "节点没有在线的反向通道"}
            ev = threading.Event()
            t.responses[req_id] = (ev, None)
            t.downlink.append({"type": "forward", "req_id": req_id,
                               "method": method, "path": path, "body": body,
                               "caller": caller, "task_id": task_id, "dims": dims})
            self._cond.notify_all()
        if not ev.wait(FORWARD_TIMEOUT_S):
            with self._cond:
                t.responses.pop(req_id, None)
            return {"error": "timeout", "message": f"节点 {FORWARD_TIMEOUT_S}s 内未回包"}
        with self._cond:
            _, payload = t.responses.pop(req_id, (None, {}))
        return payload or {"error": "empty_response"}

    def reply(self, agent_id: str, req_id: str, payload: dict) -> bool:
        with self._cond:
            for t in self._tunnels.values():
                if t.agent_id != agent_id:
                    continue
                entry = t.responses.get(req_id)
                if entry:
                    t.responses[req_id] = (entry[0], payload)
                    entry[0].set()
                    return True
        return False

    # ---- 内部 ----
    def _pick(self, agent_id: str) -> Tunnel | None:
        for t in self._tunnels.values():
            if t.agent_id == agent_id:
                return t
        return None


hub = TunnelHub()

# 协商阶梯： direct > holepunch > tunnel/relay > pull
LADDER = ["direct", "holepunch", "tunnel", "relay", "pull"]
TUNNEL_MODES = {"tunnel", "relay"}

# 回退判定用的心跳窗口（与 a2n-registry 的窗口一致）
FALLBACK_HEARTBEAT_WINDOW_S = 90.0


def _fallback_reachable(agent: dict, force: bool = False) -> tuple[bool, str]:
    """transport 被单独使用（测试、CLI）时的回退判定。

    这里刻意不 import a2n_registry：可达性的权威实现在 registry 里，
    transport 若反向依赖它就会形成环。生产环境由调用方注入
    `a2n_registry.reachability.reachable`。
    """
    c = agent.get("connection") or {}
    mode = c.get("mode", "pull")
    if mode in TUNNEL_MODES:
        return ((True, "反向通道在线") if hub.alive(agent["agent_id"])
                else (False, "反向通道已断开"))
    if mode == "direct":
        return False, "未经入站探测（请注入 registry 的 reachable）"
    last = agent.get("last_seen_at")
    if not last:
        return False, "从未心跳"
    return (False, "心跳超时") if _age(last) > FALLBACK_HEARTBEAT_WINDOW_S else (True, "心跳正常")


def _age(iso_str: str | None) -> float:
    if not iso_str:
        return 1e9
    import datetime as _dt
    try:
        t = _dt.datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return (_dt.datetime.now(_dt.timezone.utc) - t).total_seconds()
    except ValueError:
        return 1e9


def negotiate(agent: dict, is_reachable=None) -> dict:
    """给定节点，给出此刻最佳通道与完整候选清单（ICE 思路：先列全，再选优）。

    is_reachable: 可选注入 `(agent, force) -> (bool, str)`。
    不传则用回退判定 —— 生产一律由调用方注入 registry 的实现，
    以保证 transport 永远不反向依赖上层包（依赖必须单向）。
    """
    agent_id = agent["agent_id"]
    conn = agent.get("connection") or {}
    declared = conn.get("mode", "pull")
    candidates: list[dict] = []
    check = is_reachable or _fallback_reachable

    if declared in ("direct", "relay") and conn.get("url"):
        ok, why = check(agent, False)
        candidates.append({"transport": "direct", "usable": ok, "detail": why})
    else:
        candidates.append({"transport": "direct", "usable": False, "detail": "未声明公网入口"})

    srflx = conn.get("candidates") or []
    candidates.append({
        "transport": "holepunch",
        "usable": False,
        "detail": (f"已收集 {len(srflx)} 个反射候选（STUN）；两端 NAT 场景 v1 不出现，"
                   "打洞仅做候选枚举，且证据路径必须仍走平台") if srflx
                  else "无反射候选；两端 NAT 场景 v1 不出现",
    })

    st = hub.status(agent_id)
    tunnel_alive = hub.alive(agent_id)
    candidates.append({"transport": "tunnel", "usable": tunnel_alive,
                       "detail": "反向长连接在线" if tunnel_alive else "未建立反向通道"})
    if declared == "relay":
        candidates.append({"transport": "relay", "usable": tunnel_alive,
                           "detail": "平台公网入口 → 隧道 → 节点本地服务"})

    alive_pull, why_pull = check(agent)
    candidates.append({"transport": "pull", "usable": alive_pull, "detail": why_pull})

    chosen = next((c["transport"] for c in candidates
                   if c["usable"] and c["transport"] != "holepunch"), None)
    return {"agent_id": agent_id, "chosen": chosen, "candidates": candidates,
            "tunnel": st}
