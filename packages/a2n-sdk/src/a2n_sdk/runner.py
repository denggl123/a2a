"""常驻节点：注册 → 心跳 → 拉任务 → 执行 → 上报计量与结果。

设计要点：全程只有出站连接。注册、心跳、取活、回传都由节点发起，
家里电脑没有公网 IP、不在路由器上开任何端口，一样能接单干活。
"""
from __future__ import annotations

import inspect
import threading
import time
from typing import Any, Callable

from .client import Client
from .connection import connection_report


def _accepts_positionals(fn: Callable, n: int) -> bool | None:
    """fn 能否接受 n 个位置参数（None = 内省不了，跳过检查）。

    用途：把"处理器签名不对"从请求时的静默 TypeError 提前到启动即报错。
    只查位置参数——`self`/关键字参数不参与。
    """
    try:
        params = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return None
    if any(p.kind == p.VAR_POSITIONAL for p in params):
        return True
    pos = [p for p in params if p.kind in (p.POSITIONAL_ONLY,
                                           p.POSITIONAL_OR_KEYWORD)]
    required = sum(1 for p in pos if p.default is p.empty)
    return required <= n <= len(pos)


class Node:
    """一个节点 = 一张 Agent Card + 一组处理函数。

    用法：
        node = Node(card, handlers={"ocr-pro": my_ocr}, principal="acct:me",
                    base_url="http://127.0.0.1:18787")
        node.serve(console=True)   # 常驻，并在 http://127.0.0.1:8770 开本地管理台

    两个签名约定（启动时校验，错了立即报错而不是请求时静默失败）：
      handlers[skill] = fn(payload) —— 任务处理器，收任务载荷，返回结果
      local_agent=(port, fn)       —— 本地 HTTP 服务 fn(path, payload)

    另有一个可选的**计量连署**入口 attest_fn(task_id, node_id, dims)：
    给了它，节点交付时会用自己卡上那把钥匙签一份计量（推送与 inline 两条
    交付路径都签）；不给，则如实显示"未签名"。签名格式不在 SDK 里定义。
    """

    def __init__(self, card: dict, handlers: dict[str, Callable[[dict], Any]],
                 principal: str, base_url: str = "http://127.0.0.1:18787",
                 heartbeat_interval: int = 30,
                 visibility: str = "public",
                 discover_limit: int | None = None,
                 attest_fn: Callable[[str, str, dict], dict | None] | None = None) -> None:
        self.card = card
        self.handlers = handlers
        self.client = Client(base_url, principal=principal)
        # 上架信息（分发策略，不是能力）：可见范围 + **允许被发现的数量**。
        # 它们由平台执行，所以走注册请求参数、不写进卡 —— 平台改写卡会让节点
        # 自己的签名当场失效（签名域 = 整张卡）。
        self.visibility = visibility
        self.discover_limit = discover_limit
        self.heartbeat_interval = heartbeat_interval
        self._last_hb = 0.0
        self._hb_lock = threading.Lock()
        # 计量连署（可选）：把"节点自报"从一句自白变成可复算的证据。
        # 签名格式不在这里定义 —— 调用方注入 attest_fn(task_id, node_id, dims)，
        # 实现方是唯一口径源 a2n_p2p.attest.sign_metering。两处都要用：
        #   ① 推送通道交付（_handle → client.submit）
        #   ② inline 转发交付（TunnelClient._handle_forward，平台代提交）
        self.attest_fn = attest_fn
        # 本地观测：平台不知道、也不该知道的那部分（我侧真实体验）
        self.stats = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                      "tasks_ok": 0, "tasks_failed": 0, "calls": 0,
                      "total_ms": 0, "ttft": [], "last_error": None, "recent": []}
        self.peer_info: dict = {}
        self._tunnel_client = None
        # 签名守门：任务处理器必须能只吃 1 个位置参数（payload）。
        # 历史上容器节点把 fn(path, payload) 直接挂成任务处理器，
        # 派单路径一进来就 TypeError —— 被 except 吞掉，任务永远卡在 ASSIGNED。
        for sid, fn in self.handlers.items():
            ok = _accepts_positionals(fn, 1)
            if ok is False:
                raise TypeError(
                    f"handlers[{sid!r}] 需要多个位置参数；任务处理器只能是 fn(payload)。"
                    f"若这是本地服务处理器，请传 local_agent=(port, fn)")

    # ---- 生命周期 ----
    def serve(self, once: bool = False, poll_interval: float = 2.0,
              console: bool = True, console_port: int = 8770,
              tunnel: bool = False, local_agent: tuple[int, Any] | None = None) -> None:
        """常驻主循环。

        tunnel=True     出站建立反向长连接，任务从隧道实时推下来（仍保留长轮询兜底）
        local_agent=(port, fn) 把处理函数挂成本地 HTTP 服务，走 relay 中继转发：
                        外部经平台公网入口调用，本机零端口暴露
        """
        agent = self.client.register(self.card, self.visibility, self.discover_limit)
        self.client.node_id = agent["agent_id"]
        print(f"[a2n] 已注册 {agent['agent_id']} 状态={agent['status']} 能力="
              f"{[s['id'] for s in self.card.get('skills', [])]}")

        if local_agent:
            from .transport import TunnelClient, serve_local_agent
            port, fn = local_agent
            ok = _accepts_positionals(fn, 2)
            if ok is False:
                raise TypeError("local_agent 处理器必须是 fn(path, payload) 两个位置参数")
            srv = serve_local_agent(port, fn)
            local_base = f"http://127.0.0.1:{srv.server_address[1]}"
            self._tunnel_client = TunnelClient(self.client, self._handle, local_base=local_base,
                                               on_execute=self._record_forward_exec,
                                               attest_fn=self.attest_fn)
            self._tunnel_client.start()
            print(f"[a2n] 本地服务 {local_base} + 反向隧道（relay：平台公网入口 → 隧道 → 本地）")
        elif tunnel:
            from .transport import TunnelClient
            self._tunnel_client = TunnelClient(self.client, self._handle,
                                               attest_fn=self.attest_fn)
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

    def _record_forward_exec(self, ok: bool, ms: int, skill: str = "") -> None:
        """本机观测：inline 交付（隧道转发就地执行）也算干活。

        背景：v1 主链路是编排层经通道转发、平台代提交（delivery=inline），
        任务推送通道基本不走 —— 若只统计 _handle，自报 TTFT 窗口永远是空的
        （实测 samples 恒 0）。转发执行与推送执行对节点是同一件事：干了一次活。
        ok 口径：本地服务 HTTP < 400 = 干成了（验收结论仍归平台）。
        """
        self.stats["tasks_ok" if ok else "tasks_failed"] += 1
        self.stats["calls"] += 1
        self.stats["total_ms"] += ms
        self.stats["ttft"] = ([ms] + self.stats["ttft"])[:20]
        self.stats["recent"] = ([{"task_id": None, "skill": skill or None, "ms": ms,
                                  "ttft_ms": ms, "passed": bool(ok), "amount": None,
                                  "at": time.strftime("%H:%M:%S")}]
                                + self.stats["recent"])[:20]

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
    def _report_fail(self, task_id: str, reason: str) -> None:
        """如实上报失败：干不成必须说出来（否则任务卡在 ASSIGNED、预算冻着）。

        best-effort：上报本身失败只记 last_error，不再抛（主循环不能死）。
        """
        try:
            self.client.fail(task_id, reason[:180])
        except Exception as e:  # noqa: BLE001
            self.stats["last_error"] = f"{task_id} 失败上报未送达: {e}"

    def _attest(self, task_id: str, dims: dict) -> dict | None:
        """给这一单的计量连署（没有身份 / 签不动 → None，绝不伪造）。"""
        if self.attest_fn is None:
            return None
        try:
            return self.attest_fn(task_id, self.client.node_id, dict(dims))
        except Exception as e:  # noqa: BLE001 - 连署失败不拖垮交付
            self.stats["last_error"] = f"计量连署失败: {type(e).__name__}: {e}"
            return None

    def _handle(self, task: dict) -> None:
        skill = task["skill_id"]
        handler = self.handlers.get(skill)
        if not handler:
            print(f"[a2n] 无处理能力 {skill}，上报失败 {task['id']}")
            self._report_fail(task["id"], f"节点未注册技能 {skill}")
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
            self._report_fail(task["id"], f"节点执行失败：{type(e).__name__}: {e}")
            return
        ms = int((time.time() - started) * 1000)
        # 计量是节点自报的内部真相；SDK 只报自己知道的事实。
        # 不再自动带 gpu_seconds —— 能力是黑盒，SDK 无从知道是否真用了 GPU，
        # 硬报一个"墙钟秒数当 GPU 秒"是口径污染（要按 GPU 计费的节点自己报）。
        usage = self.client.meter(started, call_count=1,
                                  output_tokens=len(str(result)))
        # 连署：签的就是即将上报的这份计量内容。签不出来就不签 ——
        # 宁可如实显示"未签名"，也不塞一个假签名糊过去（那才是要禁的）。
        att = self._attest(task["id"], usage)
        if att is not None:
            usage = {**usage, "attest": att}
        try:
            r = self.client.submit(task["id"], result, usage)
        except Exception as e:  # noqa: BLE001
            self.stats["tasks_failed"] += 1
            self.stats["last_error"] = f"{task['id']} 提交失败: {e}"
            print(f"[a2n] 提交失败 {task['id']}: {e}")
            self._report_fail(task["id"], f"结果提交失败：{e}")
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
                principal: str, base_url: str = "http://127.0.0.1:18787",
                attest_fn: Callable[[str, str, dict], dict | None] | None = None) -> None:
    Node(card, handlers, principal, base_url, attest_fn=attest_fn).serve()
