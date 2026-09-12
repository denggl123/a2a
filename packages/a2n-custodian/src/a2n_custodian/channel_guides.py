"""使用方绑定引导：每个渠道"怎么绑、绑什么"的产品知识（注册制扩展点）。

边界（铁律二）：A2N 只记录绑定声明，不碰钱、不验真伪。
引导页回答的是"这个渠道要填哪些字段、去哪里拿"——这是使用端产品引导
（表单 schema），不是资金知识；渠道品牌知识按约定归持牌托管层。
底层存储仍是自由字符串：没有引导的渠道照样能绑（协议不设限），
引导只服务控制台表单与服务端形状校验。

自动结算：绑定 ACTIVE 即生效——调用时门禁自动选用 compatible 渠道
（agent accepts ∩ 已绑定渠道），无需任何后续动作。peer_account 与
x402 同理：配对 ACTIVE / 钱包绑定后即自动。
"""
from __future__ import annotations

import json
import re
from typing import Any, Protocol

from a2n_kernel.protocols import attrs

# ---- 引导注册表：channel → 引导定义。新增渠道 = register_guide 一条 ----
GUIDES: dict[str, dict[str, Any]] = {}
GUIDE_ORDER: list[str] = []


class ChannelGuide(Protocol):
    """渠道引导契约：注册期校验字段集，缺字段即 TypeError。

    只认字面形态（dict-with-keys）—— 持牌层与 A2N 之间通过这条契约把
    "这是不是个合法的引导"固化下来，避免运行时 silent miss。
    """
    channel: str
    label: str
    currency: str
    fields: list[dict]


def register_guide(guide: ChannelGuide) -> None:
    """注册一个渠道引导。重复注册覆盖（热更新友好）。

    入参必须满足 :class:`ChannelGuide` 契约（dict 形态 + 必有字段）；
    不是即 ``TypeError``，注册期失败比运行时 silent miss 安全。
    """
    if not isinstance(guide, dict) or not attrs(guide, ("channel", "label", "currency", "fields")):
        raise TypeError(
            "渠道引导必须是 dict 且含 channel/label/currency/fields 字段"
        )
    ch = (guide.get("channel") or "").strip().lower()
    if not ch:
        raise ValueError("渠道引导缺 channel")
    guide = dict(guide)
    guide["channel"] = ch
    if ch not in GUIDES:
        GUIDE_ORDER.append(ch)
    GUIDES[ch] = guide


def guide_of(channel: str) -> dict | None:
    return GUIDES.get((channel or "").strip().lower())


def all_guides() -> list[dict]:
    return [GUIDES[c] for c in GUIDE_ORDER]


def _f(key: str, label: str, *, ptype: str = "text", required: bool = True,
       placeholder: str = "", hint: str = "", pattern: str | None = None,
       pattern_hint: str | None = None, mask: bool = False,
       options: list[str] | None = None) -> dict:
    """引导字段定义。pattern 是给服务端与前端共用的正则；mask 字段展示时掩码。"""
    return {"key": key, "label": label, "type": ptype, "required": required,
            "placeholder": placeholder, "hint": hint, "pattern": pattern,
            "pattern_hint": pattern_hint, "mask": mask, "options": options}


# ---- 内置渠道引导 ----
register_guide({
    "channel": "alipay", "label": "支付宝", "icon": "支付宝", "currency": "CNY",
    "desc": "绑定后，调用声明 direct_pay:alipay 的 agent 时自动结算（免密代扣语义，A2N 只记账不碰钱）。",
    "steps": ["打开支付宝「我的」→ 头像进入个人信息页", "复制本机手机号或绑定邮箱作为账号",
              "回填下方表单完成绑定；绑定后调用无需再确认"],
    "fields": [
        _f("account", "支付宝账号", placeholder="手机号 / 邮箱", mask=True,
           pattern=r"^1\d{10}$|^[^@\s]+@[^@\s]+\.[^@\s]+$", pattern_hint="手机号或邮箱"),
        _f("holder", "实名", placeholder="与支付宝实名一致", hint="仅作对账声明，A2N 不核验"),
    ],
})

register_guide({
    "channel": "wechat_pay", "label": "微信支付", "icon": "微信", "currency": "CNY",
    "desc": "绑定后，调用声明 direct_pay:wechat_pay 的 agent 时自动结算。",
    "steps": ["微信「我」→ 服务 → 钱包 → 查看绑定手机号", "回填完成绑定"],
    "fields": [
        _f("account", "微信绑定手机号", placeholder="手机号", mask=True,
           pattern=r"^1\d{10}$", pattern_hint="11 位手机号"),
        _f("holder", "实名", placeholder="微信支付实名"),
    ],
})

register_guide({
    "channel": "bank_card", "label": "银行卡", "icon": "银行卡", "currency": "CNY",
    "desc": "绑定后走快捷代扣语义自动结算；卡号仅存掩码用于对账声明。",
    "steps": ["准备一张本人借记卡", "回填卡号与开户行完成绑定"],
    "fields": [
        _f("card_no", "卡号", mask=True, placeholder="借记卡卡号",
           pattern=r"^\d{12,23}$", pattern_hint="12–23 位数字"),
        _f("holder", "持卡人", placeholder="与银行卡实名一致"),
        _f("bank", "开户行", required=False, placeholder="如 工商银行"),
    ],
})

register_guide({
    "channel": "stablecoin", "label": "安全币", "icon": "安全币", "currency": "USDC",
    "desc": "绑定链上钱包后，调用支持 USDC 结算的 agent 时自动签名付款（媒介：稳定币，自动扣款已开启）。",
    "steps": ["打开你的钱包 App（演示网任一地址均可）", "复制收款地址（0x 开头）",
              "选择网络与资产，回填完成绑定；绑定后小额自动签名、无需逐笔确认"],
    "fields": [
        _f("wallet", "钱包地址", mask=True, placeholder="0x…",
           pattern=r"^0x[a-fA-F0-9]{6,66}$", pattern_hint="0x 开头的地址"),
        _f("network", "网络", ptype="select", options=["a2n-settlement", "ethereum"],
           hint="a2n-settlement 为演示网"),
        _f("asset", "资产", ptype="select", options=["USDC"]),
    ],
})

register_guide({
    "channel": "x402_wallet", "label": "x402 微支付钱包", "icon": "x402", "currency": "USDC",
    "desc": "绑定后调用只收 x402 的 agent 时自动附签名凭证（请求即付，无需预先建立任何关系）。",
    "steps": ["准备一个支持 x402 签名的钱包地址", "回填完成绑定；调用时平台下发 402 挑战、SDK 自动附凭证重试"],
    "fields": [
        _f("wallet", "钱包地址", mask=True, placeholder="0x…",
           pattern=r"^0x[a-fA-F0-9]{6,66}$", pattern_hint="0x 开头的地址"),
    ],
})

_SENSITIVE = {"account", "wallet", "card_no", "phone", "email", "ref"}


def validate_binding(channel: str, fields: dict | None) -> dict:
    """按渠道引导校验绑定字段，返回规范化后的 fields。

    无引导的未知渠道：原样通过（渠道是数据，协议不设限）。
    校验失败抛 ValueError（路由层映射 400，错误信息说清缺哪样、错在哪）。
    """
    g = guide_of(channel)
    fields = dict(fields or {})
    if not g:
        return {k: str(v) for k, v in fields.items() if str(v or "").strip()}
    norm: dict[str, Any] = {}
    for f in g["fields"]:
        v = str(fields.get(f["key"]) or "").strip()
        if f.get("options") and v:
            if v not in f["options"]:
                raise ValueError(f"{f['label']} 只能是 {'/'.join(f['options'])}，收到 {v!r}")
        if v:
            if f.get("pattern") and not re.match(f["pattern"], v):
                raise ValueError(f"{f['label']} 格式不对：{f.get('pattern_hint') or '不符合要求'}")
            norm[f["key"]] = v
        elif f.get("required"):
            raise ValueError(f"{g['label']}绑定缺少必填项：{f['label']}"
                             + (f"（{f['hint']}）" if f.get("hint") else ""))
    return norm


def ref_of(fields: dict) -> str:
    """字段 → ref 存储。单字段存裸值（可读）；多字段存 JSON。"""
    if len(fields) == 1:
        return next(iter(fields.values()))
    return json.dumps(fields, ensure_ascii=False)


def ref_detail(ref: str | None) -> dict | str | None:
    """ref → 结构化（JSON 则解析；否则原样返回字符串）。"""
    if ref is None:
        return None
    try:
        d = json.loads(ref)
        return d if isinstance(d, dict) else ref
    except ValueError:
        return ref


def masked_ref(ref: str | None) -> str:
    """展示掩码：JSON 逐字段（敏感字段掩码）；裸值整体掩码。"""
    d = ref_detail(ref)
    if isinstance(d, dict):
        out = {}
        for k, v in d.items():
            out[k] = _mask(str(v)) if k in _SENSITIVE else v
        return json.dumps(out, ensure_ascii=False)
    return _mask(str(ref or ""))


def _mask(s: str) -> str:
    if len(s) <= 4:
        return "****"
    return s[:3] + "****" + s[-4:]
