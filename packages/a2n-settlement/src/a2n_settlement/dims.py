"""计量维度注册表：一次调用能被按什么口径计量、哪些口径可以拿来计算钱。

为什么是注册表而不是散在代码里的 set：
    维度会随业务长出来。今天是调用次数与 token，明天可能是音频分钟、
    图片张数、检索条数、GPU 显存峰值……每加一种就回结算域改一次代码，
    等于把"什么能收钱"的判断权交给改代码的人。注册表把这件事变成数据：
    **新增维度 = register_dimension 一条，结算、计价、核验全部自动认识它。**

三条性质（沿用 SDK 与多维计量设计，纪律不变）：
    可计量 measurable   能数出来
    可验证 verifiable   平台侧能独立复算（不是节点自报什么就信什么）
    可计费 billable     **只有可计量 + 可验证的维度才允许进分账**

仅可计量但不可验证的（gpu_seconds、vram_peak_gb 这类）只能进统计与信誉，
永远不能变成钱 —— 这不是保守，是"没有可复算的证据就不许产生金额"（INV-S1）。
"""
from __future__ import annotations

from typing import Any, Protocol

from a2n_kernel.protocols import attrs

# key → 维度定义
DIMS: dict[str, dict[str, Any]] = {
    # ---- 可计费（可计量 + 可验证）----
    "call_count":    {"unit": "call",  "verifiable": True,  "billable": True,  "label": "调用次数"},
    "page_count":    {"unit": "page",  "verifiable": True,  "billable": True,  "label": "页数"},
    "char_count":    {"unit": "char",  "verifiable": True,  "billable": True,  "label": "字符数"},
    "input_tokens":  {"unit": "token", "verifiable": True,  "billable": True,  "label": "输入 token"},
    "output_tokens": {"unit": "token", "verifiable": True,  "billable": True,  "label": "输出 token"},
    "audio_seconds": {"unit": "s",     "verifiable": True,  "billable": True,  "label": "音频秒数"},
    "frame_count":   {"unit": "frame", "verifiable": True,  "billable": True,  "label": "帧数"},
    "net_out_bytes": {"unit": "byte",  "verifiable": True,  "billable": True,  "label": "出网字节"},
    # ---- 仅可计量（平台侧无法独立复算）→ 只进统计与信誉 ----
    "gpu_seconds":   {"unit": "s",     "verifiable": False, "billable": False, "label": "GPU 秒"},
    "cpu_seconds":   {"unit": "s",     "verifiable": False, "billable": False, "label": "CPU 秒"},
    "vram_peak_gb":  {"unit": "gb",    "verifiable": False, "billable": False, "label": "显存峰值"},
    "wall_time_ms":  {"unit": "ms",    "verifiable": False, "billable": False, "label": "墙钟耗时"},
    "retries":       {"unit": "count", "verifiable": False, "billable": False, "label": "重试次数"},
}


def _rebuild_billable() -> set[str]:
    return {k for k, v in DIMS.items() if v.get("billable") and v.get("verifiable")}


# 兼容旧引用：现有代码用 `dim in BILLABLE_DIMS` 判断能否计费。
# 注册新维度时同步刷新这个集合，老代码不用改就认识新维度。
BILLABLE_DIMS: set[str] = _rebuild_billable()


class UnknownDimension(ValueError):
    """用了没注册的计量维度。"""


class Dimension(Protocol):
    """维度契约：注册期校验。缺 key 即 TypeError。

    ``unit`` 是推荐字段（不强必填）：未声明时 DIMS 里默认空串，
    与既有"只声明 key/verifiable/billable"的注册方式兼容。
    """
    key: str


def register_dimension(definition: Dimension) -> dict:
    """注册一个新维度（部署期扩展点）。

    billable 默认 False —— 想让一种新维度能收钱，必须同时声明
    verifiable=True 且 billable=True，缺一不可。默认不允许收钱，
    是因为"默认可计费"会让不可复算的自报数据悄悄变成金额。

    入参必须是 :class:`Dimension`（dict 形态 + 必有字段）；不是即 ``TypeError``。
    """
    if not isinstance(definition, dict) or not attrs(definition, ("key",)):
        raise TypeError("维度定义必须是 dict 且含 key 字段")
    key = (definition or {}).get("key") or ""
    if not key:
        raise ValueError("维度缺少 key")
    # unit 可不传（默认空串 = 无单位），但 key 必填；此处不补默认值，留给合并时给
    merged = {"unit": "", "verifiable": False, "billable": False,
              "label": key, **definition}
    DIMS[key] = merged
    BILLABLE_DIMS.clear()
    BILLABLE_DIMS.update(_rebuild_billable())
    return merged


def get_dimension(key: str) -> dict | None:
    return DIMS.get(key)


def exists(key: str) -> bool:
    return key in DIMS


def is_verifiable(key: str) -> bool:
    return bool((DIMS.get(key) or {}).get("verifiable"))


def is_billable(key: str) -> bool:
    """能否进分账：既要有据可查（可验证），也要被声明为可计费。"""
    d = DIMS.get(key) or {}
    return bool(d.get("billable")) and bool(d.get("verifiable"))


def list_dims(billable_only: bool = False) -> list[dict]:
    out = []
    for k, v in DIMS.items():
        if billable_only and not is_billable(k):
            continue
        out.append({"key": k, **v})
    return out


def filter_billable(dims: dict) -> dict:
    """只留可计费维度（自报数据清洗，防止把不可复算的口径算进钱）。"""
    return {k: v for k, v in (dims or {}).items() if is_billable(k)}


def observed_only(dims: dict) -> dict:
    """只留不可计费但可统计的维度（进信誉与统计，不进金额）。"""
    return {k: v for k, v in (dims or {}).items()
            if exists(k) and not is_billable(k)}
