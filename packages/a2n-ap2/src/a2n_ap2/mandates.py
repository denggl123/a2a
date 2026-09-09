"""AP2 授权凭证（Mandate）的建模与链式校验。

AP2 的三种凭证构成一条**授权链**：

    Intent（我想买什么、最多花多少）
       └── Cart（具体购物车：买了什么、多少钱）
             └── Payment（给支付网络的扣款授权）

A2N 关心的是这条链能不能被**机器验证**，而不是能不能被看懂。
所以校验规则全部是可判定的：引用是否对得上、是否过期、金额是否越权、主体是否一致。

签名算法由外部注入 —— 与共识层同一条纪律：协议只定义语义，不绑死密码学库。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Callable

from a2n_kernel.hashing import canonical_json, new_id, now_iso, sha256

Verifier = Callable[[str, str, str], bool]   # (pub, msg, sig) -> bool

INTENT, CART, PAYMENT = "intent", "cart", "payment"
# 授权链必须按这个顺序，缺一环就 incomplete —— 中间任何一环缺失都意味着
# "有人拿不出他凭什么这么干"的证据。
CHAIN_ORDER = {INTENT: None, CART: INTENT, PAYMENT: CART}


class MandateError(ValueError):
    """授权链校验失败。带上具体原因，方便上层直接回给调用方。"""


@dataclass
class Mandate:
    kind: str                      # intent | cart | payment
    subject: str                   # 被代表的人（账户 / DID）
    agent: str                     # 被授权的 agent
    issuer: str                    # 签发方
    scope: dict[str, Any] = field(default_factory=dict)
    parent: str | None = None      # 上游凭证 id
    sig: str = ""
    pub: str = ""
    x_a2n: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("md"))
    issued_at: str = field(default_factory=now_iso)
    expires_at: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in CHAIN_ORDER:
            raise MandateError(f"未知的凭证类型：{self.kind}")
        if CHAIN_ORDER[self.kind] and not self.parent:
            raise MandateError(f"{self.kind} 必须引用上游 {CHAIN_ORDER[self.kind]} 凭证")

    def to_dict(self) -> dict:
        return asdict(self)

    def msg(self) -> str:
        """签名域：凭证身份 + 主体 + 约束。不含签名自身与扩展。"""
        return canonical_json({
            "kind": self.kind, "id": self.id, "subject": self.subject, "agent": self.agent,
            "issuer": self.issuer, "scope": self.scope, "parent": self.parent,
            "issued_at": self.issued_at, "expires_at": self.expires_at,
        })

    def digest(self) -> str:
        return sha256(self.msg())

    def expired(self, at: str | None = None) -> bool:
        if not self.expires_at:
            return False
        return (at or now_iso()) > self.expires_at

    def verify_sig(self, verifier: Verifier) -> bool:
        if not self.sig or not self.pub:
            return False
        return verifier(self.pub, self.msg(), self.sig)


def _require(cond: bool, why: str) -> None:
    if not cond:
        raise MandateError(why)


def validate_chain(payment: Mandate, cart: Mandate | None = None,
                   intent: Mandate | None = None, at: str | None = None,
                   verifier: Verifier | None = None) -> dict:
    """校验一条完整授权链。返回可审计的校验报告。

    顺序是 payment → cart → intent：从"要扣钱"倒推回"谁同意的"。
    任何一环缺失或不匹配都直接抛错 —— 授权链不允许"尽力而为"。
    """
    _require(payment.kind == PAYMENT, "链首必须是 payment 凭证")
    chain = [m for m in (intent, cart, payment) if m is not None]

    # 1. 引用闭合
    if cart is not None:
        _require(payment.parent == cart.id, f"payment 未引用该 cart：{payment.parent} != {cart.id}")
    if intent is not None and cart is not None:
        _require(cart.parent == intent.id, f"cart 未引用该 intent：{cart.parent} != {intent.id}")
    if intent is not None and cart is None:
        _require(payment.parent is not None, "存在 intent 时 payment 必须挂在 cart 之下")

    # 2. 主体一致：整条链必须服务于同一个人、同一个 agent
    subjects = {m.subject for m in chain}
    agents = {m.agent for m in chain}
    _require(len(subjects) == 1, f"授权链主体不一致：{subjects}")
    _require(len(agents) == 1, f"授权链代理不一致：{agents}")

    # 3. 时效
    for m in chain:
        _require(not m.expired(at), f"{m.kind} 凭证已过期（expires_at={m.expires_at}）")

    # 4. 金额不越权：两个方向都锁死
    if intent is not None and cart is not None:
        cap = intent.scope.get("max_amount_fen")
        total = cart.scope.get("total_fen")
        if cap is not None and total is not None:
            _require(int(total) <= int(cap), f"cart 总价 {total} 超出 intent 授权上限 {cap}")
    if cart is not None:
        total = cart.scope.get("total_fen")
        pay = payment.scope.get("amount_fen")
        if total is not None and pay is not None:
            _require(int(pay) <= int(total), f"支付授权 {pay} 超出购物车总价 {total}：想多扣")

    # 5. 签名（给了验签器才验，没给就只做结构校验 —— 有些场景凭证由上游网关已验过）
    sigs: dict[str, bool] = {}
    if verifier is not None:
        for m in chain:
            sigs[m.kind] = m.verify_sig(verifier)
        bad = [k for k, v in sigs.items() if not v]
        _require(not bad, f"凭证签名无效：{bad}")

    return {
        "ok": True,
        "subject": payment.subject,
        "agent": payment.agent,
        "chain": [m.id for m in chain],
        "amount_fen": (cart.scope.get("total_fen") if cart else None)
        or payment.scope.get("amount_fen"),
        "max_amount_fen": (intent.scope.get("max_amount_fen") if intent else None),
        "signatures": sigs,
        "checked_at": at or now_iso(),
    }
