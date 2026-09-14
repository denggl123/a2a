"""计量签名（attest）：把"节点自报"从一句自白，变成**可复算的证据**。

背景是硬伤：`usage_reports.signature` 长期是一个写死的假值，于是"可验证"的真实
含义只是"平台自观测 vs 节点自报的比对" —— 不是证据。这一处补上之后，节点用
**自己卡上那把钥匙**签"任务号 + 计量内容"，平台验签；**验不过的进争议，不进已对账**。

三条纪律：

  1. **签名域是整份 attestation payload**（canonical json），不是它的子集。
     少签一个字段 = 那个字段可以被改，而验签照样通过。
  2. **口径只有一份**：指纹走 `fingerprint_of`、验签走 `verify_pub` —— 与
     Envelope / Identity / 卡自证**同一套**。本模块不新增任何加密算法。
  3. **身份自洽**：did 必须由 attestation 自带的公钥推出。否则谁都能用自己的
     钥匙签一份写着别人 did 的报告，"验签通过"会变成一句讽刺。
"""
from __future__ import annotations

import base64
from typing import Any

from .envelope import verify_pub
from .identity import DID_PREFIX, fingerprint_of

# 自证信息挂在 x-a2n.sovereign（与 a2n-node.card 同一个槽位、同一种形状）：
# 平台模式与无托管模式**必须是同一套身份**，否则同一个节点在两种模式下会算出两个身份。
IDENTITY_SLOT = "sovereign"
SOV = IDENTITY_SLOT   # 旧名（a2n-node.card 历来用 SOV），保持一个名字两处可读

# 签名域字段：任务号 + 计量内容（计划里说的"计量内容 + 任务号"）。
METER_KEYS = ("task_id", "node_id", "dims", "at")


def pub_b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def pub_unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def did_from_pub(pub_raw: bytes) -> str:
    """公钥 → 身份。与 Identity.did 同一条推导（同一套身份，不是第二套）。"""
    return DID_PREFIX + "ag_" + fingerprint_of(pub_raw)


# ---- 从卡里取"这个节点的钥匙"（卡自证与计量验签共用同一处取数）----

def sovereign_ext(card: dict) -> dict:
    return ((card or {}).get("x-a2n") or {}).get(IDENTITY_SLOT) or {}


def card_pub_raw(card: dict) -> bytes | None:
    b64 = sovereign_ext(card).get("pub")
    if not b64:
        return None
    try:
        return pub_unb64(b64)
    except (ValueError, TypeError):
        return None


def card_did(card: dict) -> str | None:
    return sovereign_ext(card).get("did") or None


# ---- 计量报告（attestation）----

def metering_payload(*, task_id: str, node_id: str, dims: dict,
                     at: float | None = None) -> dict[str, Any]:
    """签名域：任务号 + 计量内容（+ 时刻，信息性）。

    刻意**不签 result_hash**：结果哈希口径归平台（a2n-kernel.hashing），节点算不出
    同一份哈希 —— 让节点去复刻平台的口径，只会造出第二份哈希实现，那正是要禁的。
    这里绑定的"任务号 + 计量"已经足够回答"这份计量是不是这个节点为这一单签的"。
    """
    return {"task_id": task_id, "node_id": node_id,
            "dims": dict(dims or {}),
            "at": round(at, 6) if at is not None else None}


def sign_metering(identity, *, task_id: str, node_id: str, dims: dict,
                  at: float | None = None) -> dict[str, Any]:
    """节点侧：签一份计量报告。identity 需有 .sign(dict)->str / .pub_raw / .did。

    返回自包含的一份 attestation（带 did/pub，验证方无需先交换密钥）。
    """
    payload = metering_payload(task_id=task_id, node_id=node_id, dims=dims, at=at)
    return {"did": identity.did, "pub": pub_b64(identity.pub_raw),
            "sig": identity.sign(payload), "payload": payload}


def _dims_match(a: dict, b: dict) -> bool:
    """计量内容比对：数值按 float 比（JSON 往返 1 与 1.0 是同一个数）。"""
    if set(a or {}) != set(b or {}):
        return False
    for k in a or {}:
        try:
            if float(a[k]) != float(b[k]):
                return False
        except (TypeError, ValueError):
            return False
    return True


def verify_metering(att: dict | None, *, expect_task_id: str | None = None,
                    expect_node_id: str | None = None,
                    expect_dims: dict | None = None) -> tuple[bool, str]:
    """平台侧：验签并核对"这份计量确实是为这一单签的"。

    只验签不核对内容等于没验 —— 节点可以用一份合法签名去替**另一单**背书。
    所以四步缺一不可：形状 → 身份自洽 → 内容对得上这一单 → 签名。
    """
    if not isinstance(att, dict):
        return False, "没有计量签名（节点未签名）"
    payload = att.get("payload")
    if not isinstance(payload, dict):
        return False, "计量签名缺 payload"
    extra = set(payload) - set(METER_KEYS)
    if extra:
        return False, f"计量签名域含未知字段：{sorted(extra)}"
    for k in ("task_id", "node_id", "dims"):
        if k not in payload:
            return False, f"计量签名域缺 {k}"

    pub_b64_s = att.get("pub")
    if not pub_b64_s or not att.get("sig"):
        return False, "计量签名缺公钥或签名"
    try:
        pub_raw = pub_unb64(pub_b64_s)
    except (ValueError, TypeError):
        return False, "计量签名的公钥无法解码"
    if att.get("did") and att["did"] != did_from_pub(pub_raw):
        return False, "计量签名的 did 与公钥指纹不符"

    if expect_task_id is not None and payload["task_id"] != expect_task_id:
        return False, f"计量签的是另一单（{payload['task_id']}）"
    if expect_node_id is not None and payload["node_id"] != expect_node_id:
        return False, f"计量签的是另一个节点（{payload['node_id']}）"
    if expect_dims is not None and not _dims_match(payload.get("dims"), expect_dims):
        return False, "计量内容与提交的自报不一致"

    if not verify_pub(pub_raw, payload, att["sig"]):
        return False, "计量签名无效（内容被改过）"
    return True, "ok"
