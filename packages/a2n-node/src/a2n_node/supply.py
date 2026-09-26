"""把一批"案例供给"装到一个节点上 —— **幂等**，且与控制台按钮走同一条命令。

为什么单独一个模块
------------------
两个启动入口（容器的 `docker/node_entry.py`、公网机的 `scripts/serve_public_node.py`）
都要做同样两件事：挂供给、开公益开关。各写一份就会漂，而且**都漏了同一件事**：

    RuntimeManagement.restore() 在启动时会把上次的挂载从库里重新挂上
    （`management.py:94`，逐条 mount_http），而节点目录（`A2N_HOME/runtime.db`）
    是挂卷/持久化的。所以**同一个节点目录第二次启动**时，挂载表里已经有这些
    service_id 了；此时再照旧打一次 `/v1/bindings/http`，会撞上
    `BindingTable.add` 的既有性检查（`upstream.py:421`，"拒绝静默覆盖"），
    抛 `ValueError: service_id 已存在` —— **节点直接起不来**（2026-09-26 在公网机上
    真踩到：第一次起是好的，因为目录是新的；一重启就崩）。

`BindingTable.add` 拒绝覆盖是**对的**（静默改绑等于让人以为服务的还是原来那台）。
要修的是**调用方**：先看挂载表，
* 已在、且上游一致 → 这次启动不需要做任何事（幂等）；
* 已在、但上游不同 → 响亮报错，绝不悄悄改绑；
* 不在 → 走控制台那条命令挂上去。

顺带把"开公益开关"也收在这里，保证"启动时做的"与"控制台按钮做的"是同一条路径
（`/v1/public-service`），不会长出第二套语义。
"""
from __future__ import annotations

from typing import Any, Callable, Sequence

from a2n_sdk.projection import stable_service_id

Logger = Callable[[str], None]


def _emit(tag: str, message: str, log: Logger | None) -> None:
    (log or print)(f"[{tag}] {message}")


def supply_id(daemon: Any, card: dict, endpoint: str, protocol: str = "a2a") -> str:
    """这份供给在本节点的 service_id（与 `NodeRuntime.mount_http` 的派生一致）。

    `stable_service_id(node_did, card, f"{protocol}:{endpoint}")` 是**确定性**的：
    同一个节点身份 + 同一张卡 + 同一个上游 ⇒ 同一个 id。这正是重启后能对上的前提。
    """
    return stable_service_id(daemon.identity.did, card, f"{protocol}:{endpoint}")


def mount_supply(daemon: Any, identity: Any, profiles: Sequence,
                 *, build_card: Callable[[Any, Any], dict], endpoint: str,
                 tag: str = "node", log: Logger | None = None) -> int:
    """把每份案例挂成本节点供给；已挂且上游一致就跳过。返回本次**新挂**的份数。

    `profiles` 是案例组里的成员档案（`(slug, name, skill, ...)`），
    `build_card(profile, identity)` 给出该档案的 agent card。
    """
    mounted = 0
    for profile in profiles:
        slug, name, skill = profile[0], profile[1], profile[2]
        card = build_card(profile, identity)
        sid = supply_id(daemon, card, endpoint)
        existing = daemon.runtime.bindings.get(sid)
        if existing is not None:
            have = (existing.metadata or {}).get("endpoint")
            if have != endpoint:
                raise SystemExit(
                    f"[{tag}] {name} 已有同 id 挂载（{sid}）但上游不同："
                    f"{have!r} != {endpoint!r} —— 拒绝静默改绑")
            _emit(tag, f"{name} 已在挂载表（同一上游 {endpoint}）—— 重启幂等，跳过", log)
            continue
        status, out = daemon.management.command(
            "/v1/bindings/http",
            {"card": card, "endpoint": endpoint, "protocol": "a2a"})
        if status not in (200, 201):
            raise SystemExit(f"[{tag}] 挂载 {name} 失败：{status} {out}")
        _emit(tag, f"已挂载 {name} · {skill} · 上游 {endpoint}（按设计连不上）", log)
        mounted += 1
    return mounted


def open_public_service(daemon: Any, public_base: str | None, *,
                        enabled: bool = True, tag: str = "node",
                        log: Logger | None = None) -> bool:
    """打开「公益开关」（自愿公共目录），并如实报出目录地址或没开的原因。

    `/public/v1/agents` 的两道闸门都在 `RuntimeManagement.public_directory`：
    `public_service_enabled` 与 `discovery_public_base`，缺一个就 403/400。
    这里走的是控制台按钮打的**同一条**命令（`/v1/public-service`），
    而不是绕过后台直写 store。命令本身是幂等的（写同一个布尔值）。
    """
    if not (enabled and public_base):
        _emit(tag, "公益开关未开（未声明公开入口或显式关闭）"
                   "—— 本节点不会被别的节点当目录源", log)
        return False
    status, out = daemon.management.command("/v1/public-service", {"enabled": True})
    if status != 200 or not out.get("enabled"):
        raise SystemExit(f"[{tag}] 打开公益开关失败：{status} {out}")
    _emit(tag, f"公益开关已开 · 目录地址 {public_base}/public/v1/agents"
               f"（别的节点控制台「连接节点」填 {public_base} 即可）", log)
    return True
