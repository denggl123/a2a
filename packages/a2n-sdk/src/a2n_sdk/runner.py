"""常驻节点：注册 → 心跳 → 拉任务 → 执行 → 上报计量与结果。

设计要点：全程只有出站连接。注册、心跳、取活、回传都由节点发起，
家里电脑没有公网 IP、不在路由器上开任何端口，一样能接单干活。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from .client import Client
from .connection import connection_report


class Node:
    """一个节点 = 一张 Agent Card + 一组处理函数。

    用法：
        node = Node(card, handlers={"ocr-pro": my_ocr}, principal="acct:me",
                    base_url="http://127.0.0.1:8000")
        node.serve(console=True)   # 常驻，并在 http://127.0.0.1:8770 开本地管理台
    """

    def __init__(self, card: dict, handlers: dict[str, Callable[[dict], Any]],
                 principal: str, base_url: str = "http://127.0.0.1:8000",
                 heartbeat_interval: int = 30) -> None:
        self.card = card
        self.handlers = handlers
        self.client = Client(base_url, principal=principal)
        self.heartbeat_interval = heartbeat_interval
        self._last_hb = 0.0
        self._hb_lock = threading.Lock()
        # 本地观测：平台不知道、也不该知道的那部分（我侧真实体验）
        self.stats = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                      "tasks_ok": 0, "tasks_failed": 0, "calls": 0,
                      "total_ms": 0, "ttft": [], "last_error": None, "recent": []}
        self.peer_info: dict = {}
        self._tunnel_client = None

    # ---- 生命周期 ----
    def serve(self, once: bool = False, poll_interval: float = 2.0,
              console: bool = True, console_port: int = 8770,
              tunnel: bool = False, local_agent: tuple[int, Any] | None = None) -> None:
        """常驻主循环。

        tunnel=True     出站建立反向长连接，任务从隧道实时推下来（仍保留长轮询兜底）
        local_agent=(port, fn) 把处理函数挂成本地 HTTP 服务，走 relay 中继转发：
                        外部经平台公网入口调用，本机零端口暴露
        """
        agent = self.client.register(self.card)
        self.client.node_id = agent["agent_id"]
        print(f"[a2n] 已注册 {agent['agent_id']} 状态={agent['status']} 能力="
              f"{[s['id'] for s in self.card.get('skills', [])]}")

        if local_agent:
            from .transport import TunnelClient, serve_local_agent
            port, fn = local_agent
            srv = serve_local_agent(port, fn)
            local_base = f"http://127.0.0.1:{srv.server_address[1]}"
            self._tunnel_client = TunnelClient(self.client, self._handle, local_base=local_base)
            self._tunnel_client.start()
            print(f"[a2n] 本地服务 {local_base} + 反向隧道（relay：平台公网入口 → 隧道 → 本地）")
        elif tunnel:
            from .transport import TunnelClient
            self._tunnel_client = TunnelClient(self.client, self._handle)
            self._tunnel_client.start()
            print("[a2n] 反向长连接已启动（任务经隧道实时下发，长轮询兜底）")

        if console:
            from .console import LocalConsole
            ui = LocalConsole(self, port=console_port)
            ui.start()
            print(f"[a2n] 本地管理台 http://127.0.0.1:{ui.port} （只看本机，不经过平台）")

        while True:
            now = time.time()
            if now - self._last_hb > self.heartbeat_interval:
                self._heartbeat()
                self._last_hb = now
            # 长轮询兜底：隧道断开时任务仍能被拉到。
            # 平台重启/网络抖动只退避重试，绝不退出 —— 节点必须是打不死的小强。
            try:
                for task in self.client.pending_tasks(wait=15.0 if not (tunnel or local_agent) or once else 5.0):
                    self._handle(task)
                self.stats["last_error"] = None
            except Exception as e:  # noqa: BLE001
                self.stats["last_error"] = f"平台不可达: {type(e).__name__}"
                time.sleep(3.0)
            if once:
                return

    def _metrics(self) -> dict:
        """服务质量自报：TTFT 滑动窗口平均（最近 20 次）随心跳带出。

        口径：节点自报是内部真相。TTFT = 从接到任务到首个产出（流式取首块，
        一次性 handler 退化为结果就绪时刻）。平台显示时必须标注"自报"。
        """
        ttft = self.stats.get("ttft") or []
        return {"ttft_avg_ms": int(sum(ttft) / len(ttft)) if ttft else None,
                "ttft_samples": len(ttft),
                "avg_ms": int(self.stats["total_ms"] / self.stats["calls"])
                          if self.stats["calls"] else None,
                "tasks_ok": self.stats["tasks_ok"],
                "tasks_failed": self.stats["tasks_failed"],
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    def _heartbeat(self) -> None:
        with self._hb_lock:
            try:
                report = connection_report(self.card.get("url"))
                if self._tunnel_client:
                    # 隧道在线时如实上报模式；relay = 隧道 + 平台公网中继入口
                    report["mode"] = "relay" if self._tunnel_client.local_base else "tunnel"
                report["metrics"] = self._metrics()
                r = self.client.heartbeat(report)
                self.peer_info = r
                self.stats["last_error"] = None
            except Exception as e:  # noqa: BLE001 - 心跳失败不能拖死主循环
                self.stats["last_error"] = f"心跳失败: {e}"

    # ---- 执行 ----
    def _handle(self, task: dict) -> None:
        skill = task["skill_id"]
        handler = self.handlers.get(skill)
        if not handler:
            print(f"[a2n] 无处理能力 {skill}，跳过 {task['id']}")
            return
        started = time.time()
        try:
            result = handler(task.get("payload") or {})
            # 首响 TTFT：流式 handler 取首块产出时刻；一次性 handler 退化为结果就绪时刻
            if result is not None and hasattr(result, "__next__"):
                first = next(result, None)
                first_ms = int((time.time() - started) * 1000)
                result = ([first] if first is not None else []) + list(result)
            else:
                first_ms = int((time.time() - started) * 1000)
        except Exception as e:  # noqa: BLE001
            self.stats["tasks_failed"] += 1
            self.stats["last_error"] = f"{task['id']}: {e}"
            print(f"[a2n] 执行失败 {task['id']}: {e}")
            return
        ms = int((time.time() - started) * 1000)
        usage = self.client.meter(started, call_count=1,
                                  output_tokens=len(str(result)),
                                  gpu_seconds=round((time.time() - started), 3))
        try:
            r = self.client.submit(task["id"], result, usage)
        except Exception as e:  # noqa: BLE001
            self.stats["tasks_failed"] += 1
            self.stats["last_error"] = f"{task['id']} 提交失败: {e}"
            return
        self.stats["tasks_ok" if r.get("passed") else "tasks_failed"] += 1
        self.stats["calls"] += 1
        self.stats["total_ms"] += ms
        self.stats["ttft"] = ([first_ms] + self.stats["ttft"])[:20]
        self.stats["recent"] = ([{"task_id": task["id"], "skill": skill, "ms": ms, "ttft_ms": first_ms,
                                  "passed": bool(r.get("passed")),
                                  "amount": r.get("amount"), "at": time.strftime("%H:%M:%S")}]
                                + self.stats["recent"])[:20]
        print(f"[a2n] 完成 {task['id']} -> {r.get('passed')} 实付 {r.get('amount')} 积分")

    # ---- 本地观测 ----
    def snapshot(self) -> dict:
        """给本地管理台用的本机全貌。"""
        s = dict(self.stats)
        s["avg_ms"] = int(s["total_ms"] / s["calls"]) if s["calls"] else 0
        s["ttft_avg_ms"] = (int(sum(s["ttft"]) / len(s["ttft"])) if s.get("ttft") else None)
        agent = None
        try:
            agent = self.client._req("GET", f"/v1/registry/agents/{self.client.node_id}")
        except Exception:  # noqa: BLE001
            pass
        balance = None
        if agent:
            try:
                balance = self.client.balance(agent["agent_id"])
            except Exception:  # noqa: BLE001
                pass
        transport_state = None
        try:
            transport_state = self.client._req("GET", f"/v1/nodes/{self.client.node_id}/transport")
        except Exception:  # noqa: BLE001
            pass
        return {
            "agent": agent,
            "balance_points": balance,
            "peer_info": self.peer_info,
            "transport": transport_state,
            "tunnel": {"connected": self._tunnel_client.connected.is_set(),
                       "tunnel_id": self._tunnel_client.tunnel_id,
                       "last_error": self._tunnel_client.last_error}
                      if self._tunnel_client else None,
            "local": s,
            "base_url": self.client.base,
        }


def run_forever(card: dict, handlers: dict[str, Callable[[dict], Any]],
                principal: str, base_url: str = "http://127.0.0.1:8000") -> None:
    Node(card, handlers, principal, base_url).serve()
