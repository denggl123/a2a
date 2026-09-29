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

`BindingTable.add` 拒绝覆盖是**对的**（同一 service_id 静默换一条别的供给等于骗人）。
要修的是**调用方**：先看挂载表，
* 已在、且上游/协议一致 → 这次启动不需要做任何事（幂等）；
* 已在、但上游/协议不同 → **同一逻辑商品换地址**（容器绿灯上游用随机端口，重启必换），
  原地更新这一份供给（service_id 不变，2026-09-29 起；见 `supply_id` / `mount_supply`）；
* 不在 → 走控制台那条命令挂上去。

顺带把"开公益开关"也收在这里，保证"启动时做的"与"控制台按钮做的"是同一条路径
（`/v1/public-service`），不会长出第二套语义。
"""
from __future__ import annotations

from typing import Any, Callable, Sequence

from a2n_sdk.projection import card_identity, stable_service_id

Logger = Callable[[str], None]


def _emit(tag: str, message: str, log: Logger | None) -> None:
    (log or print)(f"[{tag}] {message}")


def logical_product(card: dict) -> str:
    """一份供给的**逻辑商品标识** —— 与上游地址、端口、协议、文案都无关。

    统一走 `a2n_sdk.projection.card_identity`：优先取卡上由发布方身份派生的
    `x-a2n.uid`（`uuid5(did + "/market/" + slug)`），没有就退回 **名称 + 能力标识**。
    改描述、改版本、换上游地址都不改变它（2026-09-29 起不再用整卡哈希）。
    """
    return card_identity(card)


def supply_id(daemon: Any, card: dict, *, product: str | None = None) -> str:
    """这份供给在本节点的 service_id。

    **只由"节点身份 + 逻辑商品标识"决定**，与上游地址/端口/协议/文案都无关（R0-2）：

        stable_service_id(node_did, card, f"product:{card_identity(card)}")

    这正是"重启换端口不多出一张卡、也不换商品身份"的前提，也是"改一版描述不会
    把试用/样品/履历打断"的前提。以前 hint 用的是 `f"{protocol}:{endpoint}"`，容器
    绿灯上游一用随机端口，重启就派生出一个新 id，目录里同一商品于是出现新旧两张卡
    （一张死、一张活）；更早的种子还含整张卡的哈希，改一个错别字就换身份。
    """
    ident = card_identity(card, override=product)
    return stable_service_id(daemon.identity.did, card, f"product:{ident}")


def mount_supply(daemon: Any, identity: Any, profiles: Sequence,
                 *, build_card: Callable[[Any, Any], dict], endpoint: str,
                 protocol: str = "a2a", tag: str = "node",
                 log: Logger | None = None) -> int:
    """把每份案例挂成本节点供给；已挂且上游一致就跳过。返回本次**新挂/更新**的份数。

    `profiles` 是案例组里的成员档案（`(slug, name, skill, ...)`），
    `build_card(profile, identity)` 给出该档案的 agent card。

    `protocol` 必须与**真实上游**说同一种话（R0-1）：
    * `a2a` —— 上游是标准 A2A JSON-RPC（`message/send`）；
    * `json` —— 上游收普通 JSON（`{skill, payload, ...}`），即
      `a2n_sdk.greenlight.serve_http` 那一类"成品服务"。

    三种情形（2026-09-29 起，"换地址=更新同一商品"，不再一律报错）：
    * 不在挂载表 → 挂上（新建）；
    * 已在、上游与协议都一样 → 跳过（**重启幂等**）；
    * 已在、但上游/协议变了 → **原地更新**这一份供给：service_id 不变，因此按
      service_id 累积的试用/样品/信誉不被打断。这不是"改绑到别人家的服务"——
      service_id 里带着本节点身份，节点没换、商品没换，只是它现在监听的地址变了
      （容器绿灯上游用随机端口，重启必换一个）。变更**大声说出来**。
    """
    mounted = 0
    for profile in profiles:
        slug, name, skill = profile[0], profile[1], profile[2]
        card = build_card(profile, identity)
        sid = supply_id(daemon, card)
        existing = daemon.runtime.bindings.get(sid)
        if existing is not None:
            have = (existing.metadata or {}).get("endpoint")
            have_proto = (existing.metadata or {}).get("protocol")
            if have == endpoint and have_proto == protocol:
                _emit(tag, f"{name} 已在挂载表（同一上游 {endpoint} · {protocol}）"
                           "—— 重启幂等，跳过", log)
                continue
            _emit(tag, f"{name} 同一商品换上游：{have!r}/{have_proto!r}"
                       f" → {endpoint!r}/{protocol!r} —— 原地更新本机挂载"
                       "（service_id 不变，不重置试用/样品/信誉）", log)
            status, out = daemon.management.command(
                "/v1/bindings/rebind",
                {"service_id": sid, "endpoint": endpoint, "protocol": protocol})
            if status not in (200, 201):
                raise SystemExit(f"[{tag}] 更新 {name} 上游失败：{status} {out}")
            mounted += 1
            continue
        status, out = daemon.management.command(
            "/v1/bindings/http",
            {"card": card, "endpoint": endpoint, "protocol": protocol,
             "service_id": sid})
        if status not in (200, 201):
            raise SystemExit(f"[{tag}] 挂载 {name} 失败：{status} {out}")
        _emit(tag, f"已挂载 {name} · {skill} · {protocol} · 上游 {endpoint}", log)
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
