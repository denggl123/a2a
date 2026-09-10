"""M4a 可达性：一台家里电脑，凭什么接得到单？

结论先摆在这里：**A2N 不要求节点有公网入口。**

- `pull`（默认）：节点只需能出站。注册、拉任务、回传结果全部由节点发起，
  家庭宽带 / 公司 NAT / 运营商 CGNAT 全部适用，零配置、无端口映射。
- `wss`：节点出站建立一条长连接，平台复用该连接下发任务，用于降延迟。
- `direct` / `relay`：节点确实有公网 URL（云服务器，或 frp/cloudflared 隧道），
  平台定期做入站探测，探测失败即不可派单。

红线：发现是自由的（注册表照样能搜到），派单是受控的（不可达就不派）。
这两件事必须分开，否则一个关了路由映射的节点会把使用方的钱冻在半路。
"""
from __future__ import annotations

import ipaddress
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from a2n_store import conn
from a2n_kernel.hashing import now_iso
from a2n_transport import hub, negotiate

# pull/wss：心跳窗口（新节点注册后给一个宽限期，还没到第一个心跳周期）
HEARTBEAT_WINDOW_S = 90
FRESH_REGISTRATION_S = 300
# direct/relay：入站探测结果的有效期
PROBE_WINDOW_S = 300
PROBE_TIMEOUT_S = 3.0

OUTBOUND_MODES = {"pull", "wss"}
INBOUND_MODES = {"direct"}                       # 节点自报公网 URL，平台入站探测
TUNNEL_MODES = {"tunnel", "relay"}               # 反向长连接家族（relay=隧道之上的平台中继）
ALL_MODES = OUTBOUND_MODES | INBOUND_MODES | TUNNEL_MODES


def _parse_iso(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def is_public_url(url: str | None) -> bool:
    """判断一个 URL 是否可能被公网访问。内网/本机地址一律不算。

    生产注意：这是个粗判，真正防 SSRF 要在出网代理层做（见 probe_inbound）。
    """
    if not url:
        return False
    host = url.split("://", 1)[-1].split("/")[0].split(":")[0]
    if host in {"localhost", "localhost.localdomain", "::1", ""}:
        return False
    if host.endswith(".local") or host.endswith(".internal"):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True  # 域名：交给探测去证明
    return not (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def normalize_connection(raw: dict | None, card_url: str | None) -> dict:
    """归一化连接声明。

    关键防御：不允许把 127.0.0.1 / 192.168.x 声明成 direct。
    声明了也不认，直接降级为 pull —— 否则派单时必然送不进去。
    """
    raw = raw or {}
    mode = str(raw.get("mode") or "").lower()
    url = raw.get("url") or card_url
    downgraded = None

    if mode not in ALL_MODES:
        mode = "direct" if is_public_url(url) else "pull"
    if mode == "direct" and not is_public_url(url):
        downgraded = f"声明的入口 {url or '空'} 不是公网地址，已降级为 pull（节点只需出站）"
        mode, url = "pull", None

    return {
        "mode": mode,
        "url": url if mode == "direct" else None,   # relay 的公网入口在平台侧，不是节点声明的
        "nat": raw.get("nat", "unknown"),
        "local_ips": raw.get("local_ips", []),
        "candidates": raw.get("candidates", []),   # STUN 反射候选，供打洞协商枚举
        "inbound_ok_at": raw.get("inbound_ok_at"),
        "inbound_error": None,
        "rtt_ms": None,
        "downgraded": downgraded,
    }


def probe_inbound(url: str | None, timeout: float = PROBE_TIMEOUT_S) -> dict:
    """入站探测：平台主动访问节点声明的入口。

    安全：生产环境必须走带出网白名单的代理，禁止让节点诱导平台访问内网
    （SSRF）。这里只做最小演示实现。
    """
    if not url or not is_public_url(url):
        return {"ok": False, "error": "入口不是公网地址", "rtt_ms": None}
    if os.environ.get("A2N_PROBE_DISABLED"):
        # 测试/离线环境短路：不做真实出网探测。结果按"探测失败"处理，
        # 发现照样能搜到（发现自由），只是派单受控（不可达不派）。
        return {"ok": False, "error": "出网探测已禁用（A2N_PROBE_DISABLED）", "rtt_ms": None}
    target = url.rstrip("/") + "/health"
    started = time.time()
    try:
        req = urllib.request.Request(target, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ok = 200 <= resp.status < 400
        return {"ok": ok, "error": None if ok else f"HTTP {resp.status}",
                "rtt_ms": int((time.time() - started) * 1000)}
    except urllib.error.HTTPError as e:
        # 404 也算通：说明入口确实活着，只是没实现 /health
        return {"ok": e.code in (401, 403, 404, 405), "error": f"HTTP {e.code}",
                "rtt_ms": int((time.time() - started) * 1000)}
    except Exception as e:  # noqa: BLE001 - 探测就是要把一切失败都记下来
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:120], "rtt_ms": None}


def _save(agent_id: str, connection: dict) -> None:
    conn().execute("UPDATE agents SET connection=? WHERE agent_id=?",
                   (json.dumps(connection, ensure_ascii=False), agent_id))
    conn().commit()


def reachable(agent: dict, force: bool = False) -> tuple[bool, str]:
    """能否把任务送到这个节点。不可达 → 不入调度候选。

    返回 (是否可达, 人类可读原因)，原因会直接进打回/仲裁的凭证。
    """
    c = agent.get("connection") or {}
    mode = c.get("mode", "pull")

    # 反向长连接家族：隧道在线 = 可达（下行轮询本身就是活证据，比心跳更强）
    if mode in TUNNEL_MODES:
        if hub.alive(agent["agent_id"]):
            return True, "反向通道在线（无需公网 IP，平台复用出站长连接）"
        return False, "反向通道已断开（节点未轮询或掉线）"

    if mode in INBOUND_MODES:
        last = _parse_iso(c.get("inbound_ok_at"))
        fresh = last is not None and (time.time() - last) < PROBE_WINDOW_S
        if fresh and not force:
            return True, f"入站正常（{mode}，{int(time.time() - last)}s 前探测）"
        r = probe_inbound(c.get("url"))
        new = dict(c)
        new["inbound_error"] = r["error"]
        new["rtt_ms"] = r["rtt_ms"]
        new["inbound_ok_at"] = now_iso() if r["ok"] else None
        _save(agent["agent_id"], new)
        return (r["ok"], "入站正常" if r["ok"] else f"入站不可达：{r['error']}")

    # pull / wss：只看心跳。节点会自己来取，不需要任何入站通道。
    last = _parse_iso(agent.get("last_seen_at"))
    if last is None:
        reg = _parse_iso(agent.get("registered_at"))
        if reg is not None and (time.time() - reg) < FRESH_REGISTRATION_S:
            return True, "新节点宽限期（等待首个心跳）"
        return False, "从未心跳，节点可能已离线"
    gap = int(time.time() - last)
    if gap > HEARTBEAT_WINDOW_S:
        return False, f"心跳超时 {gap}s（阈值 {HEARTBEAT_WINDOW_S}s）"
    return True, f"心跳正常（{gap}s 前，{mode} 模式无需公网 IP）"


def nat_verdict(peer_ip: str | None, local_ips: list[str] | None) -> str:
    """NAT 处境：平台看得见出口 IP，节点自报本机 IP，两者一比就知道在不在 NAT 后。"""
    if not peer_ip:
        return "unknown"
    if peer_ip in (local_ips or []):
        return "public"
    return "natted"


def describe(agent: dict) -> dict[str, Any]:
    """给管理台用的连接摘要。"""
    c = agent.get("connection") or {}
    n = negotiate(agent, is_reachable=reachable)
    return {
        "mode": c.get("mode", "pull"),
        "url": c.get("url"),
        "nat": c.get("nat", "unknown"),
        "peer_ip": agent.get("peer_ip"),
        "reachable": n["chosen"] is not None,
        "reason": next((x["detail"] for x in n["candidates"]
                        if x["transport"] == n["chosen"]), None),
        "transport": n,
        "downgraded": c.get("downgraded"),
        "rtt_ms": c.get("rtt_ms"),
        "metrics": c.get("metrics"),
    }
