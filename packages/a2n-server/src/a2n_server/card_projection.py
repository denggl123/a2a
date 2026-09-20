"""服务端兼容投影：只改地址，不伪造供给方签名。

真正可自证的投影由节点 SDK 用自己的私钥生成。平台没有供给方私钥，因此这里只能
生成一个明确标注 ``attested=False`` 的兼容视图，并引用原始卡哈希。绝不能修改 URL
后继续携带原 ``sovereign.sig``——那张卡在密码学上已经不是原卡。
"""
from __future__ import annotations

import copy
from typing import Any


def platform_projection(card: dict[str, Any], *, entry: str, agent_id: str,
                        source_hash: str | None = None,
                        relay: str | None = None) -> dict[str, Any]:
    out = copy.deepcopy(card or {})
    ext = copy.deepcopy(out.get("x-a2n") or {})
    previous = copy.deepcopy(ext.get("projection") or {})
    sovereign = ext.pop("sovereign", None) or {}
    ext["origin"] = {
        "card_hash": source_hash,
        "did": sovereign.get("did") or previous.get("node_did"),
        "self_attested": bool(sovereign.get("sig")),
        "projection": {k: previous[k] for k in (
            "role", "node_did", "service_id", "source_kind", "attested"
        ) if k in previous},
    }
    chain = copy.deepcopy(previous.get("chain") or [])
    chain.append({"role": "platform-compat", "service_id": agent_id})
    ext["projection"] = {
        "version": 1,
        "role": "platform-compat",
        "agent_id": agent_id,
        "attested": False,
        "chain": chain,
        "reason": "平台只改写路由；请使用节点 SDK 导出带节点签名的本地投影",
    }
    ext["agent_id"] = agent_id
    if relay:
        ext["relay"] = relay
    ext["settle_modes"] = ext.get("accepts") or out.get("accepts") or []
    out["x-a2n"] = ext
    out["url"] = entry
    out["preferredTransport"] = "JSONRPC"
    out.setdefault("capabilities", {})["streaming"] = False
    return out
