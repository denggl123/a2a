"""容器里的「行业案例」节点 —— 「找 Agent」里那几档，现在真的跑在容器里。

为什么搬进容器（2026-09-19 用户拍板）
-------------------------------------
案例原先跑在主机上（`scripts/run_market_demo_agents.py`），看着像"平台自己摆的一堆
假货"。搬进容器之后，**控制台 → 平台 → relay 入口 → 反向隧道 → 容器内本地服务**
这一段走的是真的生产链路：注册、心跳、地址投影、门禁、拨号一个都没省略。
容器不映射任何端口，外面打不进来 —— 这正是"家里电脑没有公网 IP 也能被调用"的实证。

一台容器 = 一份机器，跑**两张卡**
---------------------------------
每个容器跑本组的两个案例，各自**一张卡、一把钥匙、一条隧道、一个本地端口**
（与主机上 `run_market_demo_agents.py` 的形状完全一致）。不合成"一张卡挂两档"：
卡的 `accepts` 是卡级的、价目是技能级的，门禁的 `charging(card)` 只看卡级 accepts，
混挂会让免费那档被判成"收费（None 结价）"直接拒掉。详见 MARKET 附近的 CONTAINERS 注释。

最后一跳**故意不通**（"伪真实，不能调用成功"）
----------------------------------------------
容器里的"能力本体"没有接任何真实 agent：卡上写着"交付一份经营报表"，容器里并没有
能产出它的东西。所以调用**必然失败**，而且必须**响亮地失败** —— 处理器直接抛错，
绝不返回一个看着像成功、其实是空壳的结果。

这一眼恰恰是演示里最该看的：网络是通的、卡是真的、地址是真的，
但"这活到底干得了吗" —— 答案如实说"干不了"。

与主机侧的关系：**只有一份案例数据**
------------------------------------
卡怎么长（技能、价目、署名域）一律来自 `scripts/run_market_demo_agents.py` 那一份
profile 数据，本文件只负责"把它跑在容器里"。**不在这里另写一套案例** ——
分叉两份就迟早对不上：控制台上看到的价格与容器里认的价格会变成两个数。

环境变量
--------
  A2N_CONTAINER     要起哪个容器（video-studio / finance-legal / play-ecom）
  A2N_PLATFORM      平台地址（容器内用 http://host.docker.internal:8000）
  A2N_PRINCIPAL     主体（默认与控制台开箱主体一致，见 run_market_demo_agents）
  A2N_STATE_DIR     钥匙落盘目录（挂卷 → 重启复用同一条上架，不多注册一条）
  A2N_PORT_BASE     容器内本地服务起始端口（两台各占一个）
  A2N_VISIBILITY    可见范围，默认 public
"""
from __future__ import annotations

import os
import queue
import sys
import threading
from pathlib import Path

# 同一份供给方程序，这次跑在容器里：镜像把它放在 /app/scripts（见 docker/Dockerfile）。
sys.path.insert(0, os.environ.get("A2N_SCRIPTS_DIR", "/app/scripts"))

import run_market_demo_agents as catalog  # noqa: E402
from run_free_demo_agents import ReusableDemoClient  # noqa: E402

from a2n_p2p import Identity  # noqa: E402
from a2n_sdk import Node  # noqa: E402

PLATFORM = os.environ.get("A2N_PLATFORM", "http://host.docker.internal:8000")
CONTAINER = os.environ.get("A2N_CONTAINER", "")
PRINCIPAL = os.environ.get("A2N_PRINCIPAL", catalog.DEFAULT_PRINCIPAL)
STATE_DIR = Path(os.environ.get("A2N_STATE_DIR", "/app/state"))
PORT_BASE = int(os.environ.get("A2N_PORT_BASE", "8787"))
VISIBILITY = os.environ.get("A2N_VISIBILITY", "public")

CONTAINERS = {c[0]: c for c in catalog.CONTAINERS}
BY_SLUG = {p[0]: p for p in catalog.MARKET}

# 最后一跳的失败原因。**要写清"这是设计如此，不是网络故障"** ——
# 否则看日志的人会去查隧道、查端口，白查半天。
DEAD_END = ("本容器是演示夹具：这张卡上的能力没有接真实 agent，"
            "最后一跳按设计就是不通的（网络与门禁都是通的，别去查隧道）。")


def _dead_end(*_args, **_kwargs):
    """能力本体：**故意**不实现。抛错而不是返回空壳 —— 后者会被读成"调用成功"。"""
    raise RuntimeError(DEAD_END)


def _identity(slug: str) -> Identity:
    """钥匙落盘在卷里（一份案例一把）。

    为什么不能每次启动现生成：uid 由钥匙派生（见 `build_card`），换了钥匙 = 换了
    uid ⇒ 平台上多出一条**新的**上架，而不是更新原来那条。重启三次，发现页上就是
    三份"经营报表"，看着像刷了屏。挂命名卷就是为了让钥匙活过 `docker rm -f`。
    """
    path = STATE_DIR / f"{slug}.key.json"
    if path.exists():
        return Identity.load(path)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    identity = Identity.generate()
    identity.save(path)
    return identity


def serve_profile(profile, port: int) -> None:
    """一份案例 = 一张卡 + 一条隧道 + 一个容器内本地端口。"""
    slug, name, skill = profile[0], profile[1], profile[2]
    identity = _identity(slug)
    card = catalog.build_card(profile, identity)
    node = Node(card, {skill: _dead_end},
                principal=PRINCIPAL, base_url=PLATFORM, visibility=VISIBILITY)
    # 复用同一条上架：重启走 update 分支，而不是再插一条新的
    node.client = ReusableDemoClient(PLATFORM, principal=PRINCIPAL)
    print(f"[market-node] {name} · {skill} · 容器内本地端口 {port} "
          f"· 最后一跳不通：{DEAD_END}", flush=True)
    node.serve(console=False, local_agent=(port, lambda path, payload: _dead_end()))


def main() -> None:
    if CONTAINER not in CONTAINERS:
        raise SystemExit(
            f"A2N_CONTAINER 必须是 {sorted(CONTAINERS)} 之一，收到 {CONTAINER!r}")
    slug, name, member_slugs = CONTAINERS[CONTAINER]
    profiles = [BY_SLUG[s] for s in member_slugs]
    print(f"[market-node] 容器 {slug}（{name}）-> {PLATFORM}，共 {len(profiles)} 份供给",
          flush=True)

    errors: queue.Queue = queue.Queue()

    def worker(profile, port: int) -> None:
        try:
            serve_profile(profile, port)
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
