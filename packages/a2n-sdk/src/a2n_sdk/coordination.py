"""协调层契约（a2n-coord/1）—— 数据形状、三层端口与额度模型。

**这一层只放形状与 ``Protocol``**，不认识 HTTP、P2P、目录、数据库或钱（与
``ports.py`` 同一纪律）。它把 [COORDINATION-RULES.md] 第 4/7/11 节与
[COORDINATION-API.md] 第 1/2 节的设计落成可编译、可测试的接口：

    业务层（买方本地策略）
        -> CoordinationPort         （本地会话：发现 / 暂停 / 续查 / 选路）
            -> CoordinationNetworkPort（网络层：连接 / 有界交换 / 安全探测 / 转送）
        -> LocalCandidatePolicy      （本地满意策略，由节点注入）

三条边界（照抄规则，不是自由发挥）：

* **协调只产生候选、来源、进度和通道方案**。搜索、评估、探测、route-plan
  都不得调用 Agent、占用试用、验收或扣款 —— ``SATISFIED`` 不等于购买。
* **一个 did = 一个节点**。``NodeRecord`` / ``Referral`` 的 ``proof`` 用节点 ed25519
  身份签，``sender_did`` 必须由公钥指纹推导（口径见 ``a2n_node.coord_identity``）。
* **SDK 保持零第三方依赖**：本模块纯 stdlib。密码学与网络由节点装配注入，
  ``tests/test_package_layers.py`` 守着这条线。

稳定商品键 = ``(provider_did, service_id)``。``service_id`` 复用
``projection.stable_service_id``：只由节点身份 + 逻辑商品身份（``card_identity``）
决定，改文案 / 版本 / 上游地址都不换身份（2026-09-29 修正）。
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable

from .projection import canonical_json

# ---------------------------------------------------------------- 协议常量

DOMAIN = "a2n-coord/1"
NODE_RECORD_DOMAIN = "a2n-coord-node/1"
REFERRAL_DOMAIN = "a2n-coord-referral/1"
ENVELOPE_V = 1
SUPPORTED_VERSIONS = ("a2n-coord/1",)

#: 六项逻辑操作（COORDINATION-API §4）。没有已知 Card 或可达目标时返回明确状态，
#: 不能删接口冒称完整支持。
OPERATIONS = (
    "HELLO", "FIND", "GET_CARD", "RESOLVE_ROUTES", "PROBE", "FORWARD_COORD",
)
RESULT_TYPES = tuple(f"{op}_RESULT" for op in OPERATIONS) + ("ERROR",)
ENVELOPE_TYPES = OPERATIONS + RESULT_TYPES

#: ``CoordRoute.channel_type``：只用于**协调消息**，不等于业务任务通道。
COORD_CHANNEL_TYPES = ("direct_https", "local_http", "coord_mailbox")
#: ``RouteDescriptor.channel_type``：业务任务实际走的传输适配器类型。
ROUTE_CHANNEL_TYPES = ("direct_a2a", "sealed_relay_a2a")

#: 搜索会话状态（规则 §7）。
SEARCH_STATES = (
    "CREATED", "RUNNING", "SATISFIED", "PAUSED", "BUDGET_REACHED",
    "FRONTIER_EXHAUSTED", "ISOLATED", "CANCELLED", "EXPIRED",
)
#: 终止态：不可再 pause/resume/cancel，只能新建搜索。
TERMINAL_SEARCH_STATES = frozenset({"CANCELLED", "EXPIRED"})
#: ``resume`` 允许的入口状态（COORDINATION-API §3）。
RESUMABLE_STATES = frozenset({
    "SATISFIED", "PAUSED", "BUDGET_REACHED", "FRONTIER_EXHAUSTED", "ISOLATED",
})

#: ``Candidate.verification``：CARD_VERIFIED 只说明声明通过验证，可调用性另判。
VERIFICATIONS = ("HINT", "CARD_VERIFIED", "CONFLICT", "EXPIRED", "INVALID")
#: 本地策略结论（COORDINATION-API §2.2）。
DECISIONS = ("SATISFIED", "CONTINUE", "NEEDS_INPUT")

#: 错误码 -> HTTP（COORDINATION-API §7）。HTTP 码不取代 body.code。
ERROR_HTTP = {
    "OK": 200, "NO_MATCH_IN_SNAPSHOT": 200,
    "INVALID_REQUEST": 400, "UNVERIFIED_IDENTITY": 401, "FORBIDDEN_TARGET": 403,
    "NOT_FOUND": 404,
    "REV_CONFLICT": 409, "RESULT_CHANGED": 409, "IDEMPOTENCY_CONFLICT": 409,
    "STATE_CONFLICT": 409,
    "CURSOR_EXPIRED": 410, "SESSION_EXPIRED": 410,
    "REQUEST_TOO_LARGE": 413, "RESPONSE_TOO_LARGE": 413,
    "UNSUPPORTED_ROUTE": 422, "UNSUPPORTED_VERSION": 426, "REV_REQUIRED": 428,
    "RATE_LIMITED": 429, "UNREACHABLE": 502, "BUSY": 503, "DEADLINE_EXCEEDED": 504,
}

#: 主规则 §11 的建议初值。**仅为 v0.1 试点值，必须配置化、记录策略版本**，
#: 不能当成已实测支撑的容量。
DEFAULT_BUDGET: dict[str, int] = {
    "remote_operations": 24,
    "received_bytes": 1_048_576,
    "duration_ms": 5_000,
    "introduction_depth": 3,
    "candidate_limit": 256,
    "probe_operations": 6,
    "max_concurrency": 3,
}

BUDGET_FIELDS = tuple(DEFAULT_BUDGET)
#: 消耗型额度：多轮累计上限，按 used+delta 累加（规则 §11）。
BUDGET_CONSUMED = ("remote_operations", "received_bytes", "duration_ms", "probe_operations")
#: 高水位型额度：单点上限，不做相加；按已观测峰值比较。
BUDGET_HIGH_WATER = ("introduction_depth", "candidate_limit", "max_concurrency")
#: 单会话安全上限（规则 §11）。
MAX_INTRO_DEPTH = 16
MAX_REFERRALS_PER_PAGE = 8
MAX_OFFERS_PER_PAGE = 10


class CoordinationError(ValueError):
    """契约校验失败（与 ``ProjectionError`` 同族）。"""


# ---------------------------------------------------------------- 公共形状
#
# 形状的 ``to_dict`` / ``from_dict`` 保持字段名与契约一致，不做驼峰转换；
# 时间字段为 Unix 秒；过期边界是 now >= expires_at 即无效（由调用方判）。


@dataclass(frozen=True, slots=True)
class CandidateKey:
    """商品身份：``(provider_did, service_id)``，两项都不可为空。

    地址、端口、描述、价格和版本变化**不得**自动改变这个键。
    """

    provider_did: str
    service_id: str

    def __post_init__(self) -> None:
        if not self.provider_did or not self.service_id:
            raise CoordinationError("CandidateKey 的 provider_did 与 service_id 都不可为空")

    @classmethod
    def of_card(cls, provider_did: str, card: dict[str, Any], *,
                hint: str = "") -> "CandidateKey":
        """从一张 Card 推出稳定商品键（复用全仓唯一口径 ``stable_service_id``）。"""
        from .projection import stable_service_id

        if not provider_did:
            raise CoordinationError("provider_did 不可为空")
        projection = (card.get("x-a2n") or {}).get("projection") or {}
        if projection.get("role") == "supply" and projection.get("service_id"):
            if projection.get("node_did") != provider_did:
                raise CoordinationError("商品所属节点与提供方不一致")
            return cls(provider_did, str(projection["service_id"]))
        return cls(provider_did, stable_service_id(provider_did, card, hint))

    def to_dict(self) -> dict[str, str]:
        return {"provider_did": self.provider_did, "service_id": self.service_id}

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> "CandidateKey":
        return cls(str(obj.get("provider_did") or ""), str(obj.get("service_id") or ""))


@dataclass(slots=True)
class CoordRoute:
    """一条**协调消息**通道声明，不等于业务任务通道。"""

    channel_type: str
    endpoint: str
    relay_did: str = ""

    def __post_init__(self) -> None:
        if self.channel_type not in COORD_CHANNEL_TYPES:
            raise CoordinationError(f"未知协调通道类型：{self.channel_type}")
        if not self.endpoint:
            raise CoordinationError("CoordRoute.endpoint 不可为空")


@dataclass(slots=True)
class NodeRecord:
    """节点自签的协调入口声明。**不是在线保证**。"""

    node_did: str
    versions: list[str]
    coord_routes: list[CoordRoute]
    operations: list[str]
    issued_at: int
    expires_at: int
    proof: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.node_did:
            raise CoordinationError("NodeRecord.node_did 不可为空")
        if not self.versions:
            raise CoordinationError("NodeRecord.versions 不可为空")
        unknown = [op for op in self.operations if op not in OPERATIONS]
        if unknown:
            raise CoordinationError(f"NodeRecord 声明了未知操作：{unknown}")

    def covers(self, version: str = DOMAIN) -> bool:
        return version in self.versions

    def proof_core(self) -> dict[str, Any]:
        """签名域：整份记录去掉顶层 proof。"""
        body = self.to_dict()
        body.pop("proof", None)
        return {"domain": NODE_RECORD_DOMAIN, "value": body}

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_did": self.node_did,
            "versions": list(self.versions),
            "coord_routes": [asdict(r) for r in self.coord_routes],
            "operations": list(self.operations),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "proof": dict(self.proof),
        }

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> "NodeRecord":
        routes = [CoordRoute(str(r.get("channel_type") or ""), str(r.get("endpoint") or ""),
                             str(r.get("relay_did") or ""))
                  for r in (obj.get("coord_routes") or [])]
        return cls(
            node_did=str(obj.get("node_did") or ""),
            versions=[str(v) for v in (obj.get("versions") or [])],
            coord_routes=routes,
            operations=[str(o) for o in (obj.get("operations") or [])],
            issued_at=int(obj.get("issued_at") or 0),
            expires_at=int(obj.get("expires_at") or 0),
            proof=dict(obj.get("proof") or {}),
        )


@dataclass(slots=True)
class Referral:
    """引荐者签名的观察。保留目标**自签**声明；不证明目标在线或质量合格。"""

    introducer_did: str
    target_record: NodeRecord
    target_record_hash: str
    skill_hint: list[str]
    observation: str
    observed_at: int
    expires_at: int
    proof: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.introducer_did:
            raise CoordinationError("Referral.introducer_did 不可为空")
        if self.introducer_did == self.target_record.node_did:
            raise CoordinationError("Referral 的引荐者与目标不能是同一节点")

    def proof_core(self) -> dict[str, Any]:
        body = self.to_dict()
        body.pop("proof", None)
        return {"domain": REFERRAL_DOMAIN, "value": body}

    def to_dict(self) -> dict[str, Any]:
        return {
            "introducer_did": self.introducer_did,
            "target_record": self.target_record.to_dict(),
            "target_record_hash": self.target_record_hash,
            "skill_hint": list(self.skill_hint),
            "observation": self.observation,
            "observed_at": self.observed_at,
            "expires_at": self.expires_at,
            "proof": dict(self.proof),
        }

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> "Referral":
        return cls(
            introducer_did=str(obj.get("introducer_did") or ""),
            target_record=NodeRecord.from_dict(obj.get("target_record") or {}),
            target_record_hash=str(obj.get("target_record_hash") or ""),
            skill_hint=[str(s) for s in (obj.get("skill_hint") or [])],
            observation=str(obj.get("observation") or ""),
            observed_at=int(obj.get("observed_at") or 0),
            expires_at=int(obj.get("expires_at") or 0),
            proof=dict(obj.get("proof") or {}),
        )


@dataclass(slots=True)
class OfferHint:
    """来源节点给出的**线索**；未取卡验签前不可调用。"""

    provider_did: str
    service_id: str
    skills: list[str]
    card_ref: dict[str, Any]
    card_hash: str
    observed_at: int

    @property
    def key(self) -> CandidateKey:
        return CandidateKey(self.provider_did, self.service_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_did": self.provider_did, "service_id": self.service_id,
            "skills": list(self.skills), "card_ref": dict(self.card_ref),
            "card_hash": self.card_hash, "observed_at": self.observed_at,
        }

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> "OfferHint":
        return cls(
            provider_did=str(obj.get("provider_did") or ""),
            service_id=str(obj.get("service_id") or ""),
            skills=[str(s) for s in (obj.get("skills") or [])],
            card_ref=dict(obj.get("card_ref") or {}),
            card_hash=str(obj.get("card_hash") or ""),
            observed_at=int(obj.get("observed_at") or 0),
        )


@dataclass(slots=True)
class VerifiedCard:
    """已通过供给方验签的 Card 绑定。"""

    card: dict[str, Any]
    card_hash: str
    verified_at: int
    provider_did: str
    service_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RouteDescriptor:
    """可调用通道方案：Card 必须由 ``provider_did`` 签名，类型须与真实适配器相符。"""

    route_id: str
    key: CandidateKey
    channel_type: str
    route_card_hash: str
    card: dict[str, Any]
    expires_at: int
    relay_did: str = ""

    def __post_init__(self) -> None:
        if not self.route_id:
            raise CoordinationError("RouteDescriptor.route_id 不可为空")
        if self.channel_type not in ROUTE_CHANNEL_TYPES:
            raise CoordinationError(f"未知业务通道类型：{self.channel_type}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id, "key": self.key.to_dict(),
            "channel_type": self.channel_type, "route_card_hash": self.route_card_hash,
            "card": dict(self.card), "expires_at": self.expires_at,
            "relay_did": self.relay_did,
        }


@dataclass(slots=True)
class RouteObservation:
    """通道观测。``None`` 表示 unknown；本机实测 / 自报 / 第三方报告必须分开。"""

    route_id: str
    measured_by: str
    at: int
    probe_kind: str
    control_reachable: bool | None = None
    task_reachable: bool | None = None
    rtt_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Candidate:
    """同商品多来源合并；Card 版本和通道分开保存。"""

    key: CandidateKey
    cards: list[VerifiedCard] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    routes: list[RouteDescriptor] = field(default_factory=list)
    verification: str = "HINT"

    def __post_init__(self) -> None:
        if self.verification not in VERIFICATIONS:
            raise CoordinationError(f"未知核验状态：{self.verification}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key.to_dict(),
            "cards": [c.to_dict() for c in self.cards],
            "sources": [dict(s) for s in self.sources],
            "routes": [r.to_dict() for r in self.routes],
            "verification": self.verification,
        }


# ---------------------------------------------------------------- 额度模型


@dataclass(frozen=True, slots=True)
class Budget:
    """一轮 / 单会话的额度上限。七个字段均为**正整数**，不得用 boolean 顶替。"""

    remote_operations: int
    received_bytes: int
    duration_ms: int
    introduction_depth: int
    candidate_limit: int
    probe_operations: int
    max_concurrency: int

    def __post_init__(self) -> None:
        for name in BUDGET_FIELDS:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise CoordinationError(f"Budget.{name} 必须是正整数，收到 {value!r}")

    @classmethod
    def defaults(cls) -> "Budget":
        return cls(**DEFAULT_BUDGET)

    def to_dict(self) -> dict[str, int]:
        return {name: int(getattr(self, name)) for name in BUDGET_FIELDS}

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> "Budget":
        return cls(**{name: obj.get(name) for name in BUDGET_FIELDS})


class BudgetLedger:
    """额度账本：**发网络操作前先预留**；预留了就算已用，崩溃重启也不返还。

    语义（规则 §11 / API §2.1）：

    * 消耗型额度（``remote_operations`` / ``received_bytes`` / ``duration_ms`` /
      ``probe_operations``）按 ``used + delta`` 累加，多轮累计不得超过总额度。
    * ``probe_operations`` 是 ``remote_operations`` 的**子额度**：一次探测同时
      消耗两者，不是额外赠送的操作。
    * 高水位型额度（``introduction_depth`` / ``candidate_limit`` /
      ``max_concurrency``）不做相加，按已观测峰值比较。

    ``reserve`` / ``observe`` 任一分量超限即**整体失败、不改账**（原子）。
    """

    def __init__(self, total: Budget) -> None:
        self.total = total
        self._used: dict[str, int] = {name: 0 for name in BUDGET_FIELDS}

    def used(self) -> dict[str, int]:
        """已用额度（0 起算的计数器，**不是** ``Budget``：限额才要求正整数）。"""
        return dict(self._used)

    def remaining(self) -> dict[str, int]:
        """剩余额度。消耗型是"总额 - 已用"；高水位型是"上限 - 已观测峰值"。

        两类都是同一个减法，区别只在语义（是否随轮次累加），故此处口径统一。
        剩余值可能为 0（已触顶），但不会为负 —— 预留时已挡住超限。
        """
        return {name: getattr(self.total, name) - self._used[name]
                for name in BUDGET_FIELDS}

    def _check(self, name: str, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CoordinationError(f"{name} 必须是非负整数，收到 {value!r}")

    def reserve(self, **deltas: int) -> bool:
        """预留（=立即记账）若干消耗型额度。任一分量超限返回 False 且不改账。"""
        for name, delta in deltas.items():
            if name not in BUDGET_FIELDS:
                raise CoordinationError(f"未知额度字段：{name}")
            self._check(name, delta)
        if any(self._used[name] + delta > getattr(self.total, name)
               for name, delta in deltas.items()):
            return False
        for name, delta in deltas.items():
            self._used[name] += delta
        return True

    def observe(self, name: str, peak: int) -> bool:
        """高水位登记：``used = max(used, peak)``。超过总额度返回 False 且不改账。"""
        if name not in BUDGET_FIELDS:
            raise CoordinationError(f"未知额度字段：{name}")
        self._check(name, peak)
        if peak > getattr(self.total, name):
            return False
        if peak > self._used[name]:
            self._used[name] = peak
        return True

    def probe(self, *, bytes_hint: int = 0, duration_hint: int = 0) -> bool:
        """一次主动探测：同扣 ``probe_operations`` 与 ``remote_operations``（子额度）。"""
        deltas = {"remote_operations": 1, "probe_operations": 1}
        if bytes_hint:
            deltas["received_bytes"] = bytes_hint
        if duration_hint:
            deltas["duration_ms"] = duration_hint
        return self.reserve(**deltas)

    def exhausted(self) -> bool:
        return any(self._used[name] >= getattr(self.total, name) for name in BUDGET_FIELDS)

    def stop_reason(self) -> str:
        """触顶的字段名；未触顶返回空串。"""
        for name in BUDGET_FIELDS:
            if self._used[name] >= getattr(self.total, name):
                return name
        return ""

    def to_dict(self) -> dict[str, Any]:
        """可持久化快照：重启后按 used 恢复，**不重置**已消耗额度。"""
        return {"total": self.total.to_dict(), "used": dict(self._used)}

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> "BudgetLedger":
        ledger = cls(Budget.from_dict(obj.get("total") or {}))
        for name in BUDGET_FIELDS:
            value = (obj.get("used") or {}).get(name, 0)
            ledger._check(name, value)
            if value > getattr(ledger.total, name):
                raise CoordinationError(f"已用额度 {name} 超过授权上限")
            ledger._used[name] = value
        return ledger


# ---------------------------------------------------------------- 搜索会话


@dataclass(slots=True)
class SearchSpec:
    """业务层给协调层的本地条件。**精确预算与私有偏好只在本机存储**。"""

    skill: str
    required: dict[str, Any] = field(default_factory=dict)
    preferences: dict[str, Any] = field(default_factory=dict)
    policy_id: str = ""
    policy_version: str = ""
    round_budget: Budget | None = None
    auto_continue: bool = False
    total_budget: Budget | None = None

    def __post_init__(self) -> None:
        if not self.skill:
            raise CoordinationError("SearchSpec.skill 不可为空")
        if self.auto_continue and self.total_budget is None:
            raise CoordinationError("auto_continue 打开时必须给出 total_budget（防无限续查）")

    def coarse(self) -> dict[str, Any]:
        """对外 FIND 只发允许公开的**粗粒度**条件；私有偏好一步都不上网。"""
        return {"skill": self.skill}


@dataclass(slots=True)
class SearchSnapshot:
    """搜索状态快照。revision / progress_revision / result_revision 三版本分治。"""

    search_id: str
    revision: int = 1
    progress_revision: int = 0
    result_revision: int = 0
    round: int = 1
    state: str = "CREATED"
    spec_version: str = ""
    budget_used: dict[str, int] = field(default_factory=dict)
    budget_remaining: dict[str, int] = field(default_factory=dict)
    candidate_count: int = 0
    frontier_count: int = 0
    stop_reason: str = ""

    def __post_init__(self) -> None:
        if self.state not in SEARCH_STATES:
            raise CoordinationError(f"未知搜索状态：{self.state}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PolicyDecision:
    """本地策略结论。可以停搜 / 继续 / 请人确认，**不能直接创建业务任务**。"""

    decision: str
    reason_codes: list[str] = field(default_factory=list)
    selected_keys: list[CandidateKey] = field(default_factory=list)
    result_revision: int = 0
    policy_version: str = ""

    def __post_init__(self) -> None:
        if self.decision not in DECISIONS:
            raise CoordinationError(f"未知策略结论：{self.decision}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason_codes": list(self.reason_codes),
            "selected_keys": [k.to_dict() for k in self.selected_keys],
            "result_revision": self.result_revision,
            "policy_version": self.policy_version,
        }


@dataclass(slots=True)
class RoutePlan:
    """通道优选结果。``control_reachable`` / ``task_reachable`` **分开**。"""

    key: CandidateKey
    plan_revision: int = 1
    choices: list[dict[str, Any]] = field(default_factory=list)
    preferred_route_id: str = ""
    reason_codes: list[str] = field(default_factory=list)
    remaining_budget: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key.to_dict(), "plan_revision": self.plan_revision,
            "choices": [dict(c) for c in self.choices],
            "preferred_route_id": self.preferred_route_id,
            "reason_codes": list(self.reason_codes),
            "remaining_budget": dict(self.remaining_budget),
        }


# ---------------------------------------------------------------- 三层端口


@runtime_checkable
class CoordinationPort(Protocol):
    """本地业务 → 协调层。全程只读本地会话，不了交易。"""

    def start(self, spec: SearchSpec, command_id: str) -> SearchSnapshot: ...
    def get(self, search_id: str) -> SearchSnapshot: ...
    def pause(self, search_id: str, command_id: str, expected_revision: int) -> SearchSnapshot: ...
    def resume(self, search_id: str, command_id: str, expected_revision: int,
               round_budget: Budget | None = None) -> SearchSnapshot: ...
    def cancel(self, search_id: str, command_id: str, expected_revision: int) -> SearchSnapshot: ...
    def evaluate(self, search_id: str, command_id: str, expected_revision: int) -> dict[str, Any]: ...
    def plan(self, search_id: str, key: CandidateKey, command_id: str,
             expected_revision: int, probe: bool = False) -> RoutePlan: ...
    def candidates(self, search_id: str, result_cursor: str = "",
                   limit: int = 20) -> dict[str, Any]: ...


@runtime_checkable
class LocalCandidatePolicy(Protocol):
    """本地满意策略。输入不可变候选快照，输出 ``PolicyDecision``。

    远端响应**不得**携带待执行的策略代码；策略只在本机跑。
    """

    def evaluate(self, candidate_snapshot: list[dict[str, Any]], policy_version: str,
                 budget_remaining: dict[str, int]) -> PolicyDecision: ...


@runtime_checkable
class CoordinationNetworkPort(Protocol):
    """协调层 → 网络层。适配器必须限制解析、重定向、地址类别、响应体和超时。"""

    def connect(self, node_record: NodeRecord, deadline: float) -> dict[str, Any]: ...
    def exchange(self, session: dict[str, Any], signed_request: dict[str, Any],
                 response_cap: int) -> dict[str, Any]: ...
    def safe_probe(self, coord_route: CoordRoute, nonce: str, deadline: float) -> RouteObservation: ...
    def probe_task_route(self, route_descriptor: RouteDescriptor, nonce: str,
                         deadline: float) -> RouteObservation: ...
    def forward(self, session: dict[str, Any], forwarding_envelope: dict[str, Any],
                response_cap: int) -> dict[str, Any]: ...


# ---------------------------------------------------------------- 工具


def query_fingerprint(skill: str, coarse_requirements: dict[str, Any] | None,
                      page_size: int) -> str:
    """FIND 的公开条件指纹：SHA-256 小写 64 位十六进制。

    **不包含** ``request_id`` 或 ``cursor``；私有偏好与总预算不上网（API §4.3）。
    """
    if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size <= 0:
        raise CoordinationError("page_size 必须是正整数")
    core = {"skill": str(skill), "coarse_requirements": coarse_requirements or {},
            "page_size": page_size}
    return hashlib.sha256(canonical_json(core).encode("utf-8")).hexdigest()


def record_hash(record: NodeRecord | dict[str, Any]) -> str:
    """``target_record_hash``：完整已签 NodeRecord 按项目 JSON 字节的 SHA-256。"""
    obj = record.to_dict() if isinstance(record, NodeRecord) else dict(record)
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def card_key_of(provider_did: str, card: dict[str, Any], *, hint: str = "") -> CandidateKey:
    """便捷入口：从 Card 派生稳定商品键（供适配器与测试共用）。"""
    return CandidateKey.of_card(provider_did, card, hint=hint)


__all__ = [
    "DOMAIN", "NODE_RECORD_DOMAIN", "REFERRAL_DOMAIN", "ENVELOPE_V",
    "SUPPORTED_VERSIONS", "OPERATIONS", "RESULT_TYPES", "ENVELOPE_TYPES",
    "COORD_CHANNEL_TYPES", "ROUTE_CHANNEL_TYPES", "SEARCH_STATES",
    "TERMINAL_SEARCH_STATES", "RESUMABLE_STATES", "VERIFICATIONS", "DECISIONS",
    "ERROR_HTTP", "DEFAULT_BUDGET", "BUDGET_FIELDS", "BUDGET_CONSUMED",
    "BUDGET_HIGH_WATER", "MAX_INTRO_DEPTH", "MAX_REFERRALS_PER_PAGE",
    "MAX_OFFERS_PER_PAGE", "CoordinationError",
    "CandidateKey", "CoordRoute", "NodeRecord", "Referral", "OfferHint",
    "VerifiedCard", "RouteDescriptor", "RouteObservation", "Candidate",
    "Budget", "BudgetLedger", "SearchSpec", "SearchSnapshot", "PolicyDecision",
    "RoutePlan", "CoordinationPort", "LocalCandidatePolicy",
    "CoordinationNetworkPort", "query_fingerprint", "record_hash", "card_key_of",
]
