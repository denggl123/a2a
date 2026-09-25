"""容器里的「行业案例」节点 —— docker 模拟公网环境里的另一端。

为什么搬进容器（2026-09-19 用户拍板）
-------------------------------------
案例原先跑在主机上，看着像"平台自己摆的一堆假货"。搬进容器之后，
**控制台 → 平台 → relay 入口 → 反向隧道 → 容器内本地服务**这一段走的是真的生产链路：
注册、心跳、地址投影、门禁、拨号一个都没省略。容器不映射任何端口，外面打不进来 ——
这正是"家里电脑没有公网 IP 也能被调用"的实证。

用户对这四个节点的定位（2026-09-20）：「**这 4 个节点只是测试，真正好了会丢到外网，
你只是用 docker 模拟公网环境**」。所以容器 = 公网上的另一台机器，而不是"平台的一部分"。

一台容器 = 一个身份，摆两张卡
-----------------------------
**一个节点一个身份**：一台容器就是一台机器，一把钥匙（`STATE_DIR/node.key.json`）
签它上架的所有卡，上架主体都是**这个节点的 did**。
（以前是一张卡一把钥匙、主体硬写成 `acct:alice` —— 那等于一台机器上住着两个"人"，
且这个"人"跟卡里自证的身份还是两回事。现在身份只有一个答案。）

两张卡不合成一张：卡的 `accepts` 是卡级的、价目是技能级的，门禁的 `charging(card)`
只看卡级 accepts，混挂会让免费那档被判成"收费（None 结价）"直接拒掉。
详见 `run_market_demo_agents.py` 的 CONTAINERS 注释。

最后一跳**真的不通**（用户 2026-09-20 原话）
----------------------------------------------
「某个节点上架了一个 agent。我这个节点发现了，看着所有都正常。只有调用时，
我 sdk 请求对方 sdk，对方节点收到请求，他去转发调用 agent 时，发现调用不通。返回失败。」

所以这一跳**不是我们预先写好的文案**：节点会**真的**去连一台不存在的上游 agent
（`A2N_DEAD_AGENT`），连不上的真实错误原样上报为失败原因。
演示里最该看的就是这一段：**发现通、对方节点活、请求也送到了；断在它转发给自己那台 agent**。
`scripts/a2a_smoke.py` 的 ⑨ 断的就是这个形状（不是断成功）。

与主机侧的关系：**只有一份案例数据**
------------------------------------
卡怎么长（技能、价目、署名域）一律来自 `scripts/run_market_demo_agents.py` 那一份
profile 数据，本文件只负责"把它跑在容器里"。**不在这里另写一套案例** ——
分叉两份就迟早对不上：控制台上看到的价格与容器里认的价格会变成两个数。

环境变量
--------
  A2N_CONTAINER     要起哪个容器（video-studio / finance-legal / play-ecom）
  A2N_PLATFORM      平台地址（容器内用 http://host.docker.internal:18787）
  A2N_PRINCIPAL     覆盖上架主体（默认 = 本容器节点自己的 did）
  A2N_DEAD_AGENT    上游 agent 地址（默认 http://127.0.0.1:9/invoke，本地没人听）
  A2N_STATE_DIR     钥匙落盘目录（挂卷 → 重启复用同一身份与同一条上架）
  A2N_PORT_BASE     容器内本地服务起始端口（两张卡各占一个）
  A2N_VISIBILITY    可见范围，默认 public
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

# 同一份供给方程序，这次跑在容器里：镜像把它放在 /app/scripts（见 docker/Dockerfile）。
sys.path.insert(0, os.environ.get("A2N_SCRIPTS_DIR", "/app/scripts"))

import run_market_demo_agents as catalog  # noqa: E402
from run_free_demo_agents import ReusableDemoClient  # noqa: E402

from a2n_p2p import Identity  # noqa: E402
from a2n_sdk import Node  # noqa: E402

PLATFORM = os.environ.get("A2N_PLATFORM", "http://host.docker.internal:18787")
CONTAINER = os.environ.get("A2N_CONTAINER", "")
PRINCIPAL = os.environ.get("A2N_PRINCIPAL")     # None = 用本节点自己的 did
DEAD_AGENT = os.environ.get("A2N_DEAD_AGENT", "http://127.0.0.1:9/invoke")
STATE_DIR = Path(os.environ.get("A2N_STATE_DIR", "/app/state"))
PORT_BASE = int(os.environ.get("A2N_PORT_BASE", "8787"))
VISIBILITY = os.environ.get("A2N_VISIBILITY", "public")

CONTAINERS = {c[0]: c for c in catalog.CONTAINERS}
BY_SLUG = {p[0]: p for p in catalog.MARKET}


def _agent_behind(payload):
    """能力本体：这台机器上**并没有**那张卡说的 agent —— 而且不是"假装没有"。

    节点会**真的**去连它（`A2N_DEAD_AGENT` 默认指向本地一个没人听的端口），
    连不上的真实错误原样抛出去，由 SDK 的 Node._handle 如实上报为 failed + 原因。

    为什么不做成"直接抛一句写死的错"：那样报出来的是我们的文案，不是这次尝试的结果；
    "参数错 / 节点坏了 / 按设计就不通"三种情况会被糊成同一种，等于没给证据。
    演示要的是**真实发生的一跳失败**。

    **必须绕开代理**：上游 agent 就在本机，走代理会得到一句 "502 Bad Gateway"
    —— 那是代理的话，不是"连不上 agent"的事实（本机常驻代理拦 127.0.0.1，真踩过）。
    """
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        DEAD_AGENT, data=body, method="POST",
        headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except Exception as e:  # noqa: BLE001 - 真实错误必须原样上行
        raise RuntimeError(
            f"转发到上游 agent（{DEAD_AGENT}）失败：{type(e).__name__}: {e}") from e


def _identity() -> Identity:
    """这台容器节点的身份。**一台机器一个**（不是一张卡一个）。

    钥匙落盘在**命名卷**里：uid 由身份派生（见 `build_card`），换了钥匙 = 换了 did
    = 换了 uid ⇒ 平台上多出一条**新的**上架，而不是更新原来那条。重启三次，
    发现页上就是三份"经营报表"。挂命名卷就是为了让这把钥匙活过 `docker rm -f`。
    """
    path = STATE_DIR / "node.key.json"
    if path.exists():
        return Identity.load(path)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    identity = Identity.generate()
    identity.save(path)
    print(f"[market-node] 新身份已生成 {path}（{identity.did}）", flush=True)
    return identity


def serve_profile(profile, identity: Identity, port: int) -> None:
    """一张卡 = 一张卡面 + 一条隧道 + 一个容器内本地端口；身份由整台机器共用。"""
    slug, name, skill = profile[0], profile[1], profile[2]
    card = catalog.build_card(profile, identity)
    node = Node(card, {skill: _agent_behind},
                principal=PRINCIPAL or identity.did, base_url=PLATFORM,
                visibility=VISIBILITY)
    # 复用同一条上架：重启走 update 分支，而不是再插一条新的
    node.client = ReusableDemoClient(PLATFORM, principal=PRINCIPAL or identity.did)
    print(f"[market-node] 上架 {name} · {skill} · 容器内本地端口 {port} · "
          f"上游 agent {DEAD_AGENT}（按设计连不上）", flush=True)
    # 注意 `_agent_behind(payload)` 要**真的把 payload 递过去** —— 演示里发生的是
    # "转发这次请求"，不是一个空壳调用。漏传参数会变成 TypeError，失败原因就成了一句
    # 我们自己的 bug（2026-09-20 真踩：冒烟 ⑨ 报 `missing 1 required positional argument`）。
    node.serve(console=False, local_agent=(port, lambda path, payload: _agent_behind(payload)))


def main() -> None:
    if CONTAINER not in CONTAINERS:
        raise SystemExit(
            f"A2N_CONTAINER 必须是 {sorted(CONTAINERS)} 之一，收到 {CONTAINER!r}")
    slug, name, member_slugs = CONTAINERS[CONTAINER]
    profiles = [BY_SLUG[s] for s in member_slugs]
    identity = _identity()
    print(f"[market-node] 容器 {slug}（{name}）· 身份 {identity.did} -> {PLATFORM}，"
          f"共 {len(profiles)} 份供给", flush=True)

    errors: queue.Queue = queue.Queue()

    def worker(profile, port: int) -> None:
        try:
            serve_profile(profile, identity, port)
        except Exception as exc:  # noqa: BLE001 - 起不来要如实报，不静默半死
            errors.put((profile[0], exc))

    for index, profile in enumerate(profiles):
        threading.Thread(target=worker, args=(profile, PORT_BASE + index),
                         name="market-" + profile[0], daemon=True).start()
    # 起不来就炸（而不是"主线程活着、节点已经死了"那种半死状态）
    failed_slug, exc = errors.get()
    raise RuntimeError(f"{failed_slug} 容器节点启动或运行失败") from exc


if __name__ == "__main__":
    main()
