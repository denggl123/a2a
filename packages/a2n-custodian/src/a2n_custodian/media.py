"""媒介与币种注册表：钱以什么形态存在，全系统只在这一处回答。

为什么放在持牌层（a2n-custodian）而不是业务层：
    “哪种钱、最小单位是多少、能不能自动扣、能不能退、A2N 看不看得见余额”
    全是资金侧的知识，而且一变就得改认识它的代码——按铁律二，这些知识
    只能住在持牌层。业务层永远只认一个 `medium_code` 字符串。

扩展性：
    新增一种钱 = 往 MEDIA 里加一条（或运行时 register），业务逻辑零改动。
    今天有三种（稳定币 / 支付宝 / 积分），明天加别的币种、别的链、别的
    渠道，都不需要动调用链、门禁、计价与记账。

三种能力的含义（决定控制台能显示什么、能自动做什么）：
    auto_pay        能否不经人工确认自动付款（稳定币签名、支付宝免密代扣）
    refundable      能否由 A2N 侧发起逆向（链上转账做不到，需对方配合）
    balance_visible 余额是否在 A2N 掌握之中（只有积分是，外部媒介一律不可知）
"""
from __future__ import annotations

from typing import Any

# code → 媒介定义。exponent = 最小单位指数：10^-exponent 是这种钱的最小刻度
MEDIA: dict[str, dict[str, Any]] = {
    "stablecoin": {
        "code": "stablecoin",
        "currency": "USDC",
        "exponent": 6,             # 链上最小单位 10^-6
        "network": "ethereum",
        "label": "稳定币",
        "auto_pay": True,
        "refundable": False,       # 链上转账无强制退款，需对方配合
        "balance_visible": False,  # 钱在外部钱包，A2N 不掌握余额
        "region_note": "境外可用",
    },
    "channel_pay": {               # 第三方支付渠道（具体品牌是数据，归本层）
        "code": "channel_pay",
        "currency": "CNY",
        "exponent": 2,             # 分
        "network": "channel",
        "label": "第三方支付",
        "auto_pay": True,          # 需使用方先签免密代扣协议
        "refundable": True,
        "balance_visible": False,
        "region_note": "境内，收款方需商家资质",
    },
    "points": {
        "code": "points",
        "currency": "POINT",
        "exponent": 0,             # 1 积分
        "network": "a2n",
        "label": "A2N 积分",
        "auto_pay": True,          # 账本内划转
        "refundable": True,
        "balance_visible": True,   # 唯一一种 A2N 掌握余额的媒介
        "region_note": "发行来源待定，一期只记账不发行",
    },
}

DEFAULT_MEDIUM = "points"
# 全系统唯一的"默认币种"口径：钱以什么形态存在是资金侧知识，
# 各处不许各写一份 "CNY" —— 缺币种信息时一律引用这里。
DEFAULT_CURRENCY = "CNY"


class UnknownMedium(ValueError):
    """业务层传了没注册过的媒介 —— 宁可拒绝，也不猜。"""


def register_medium(definition: dict) -> dict:
    """注册一种新媒介（部署期扩展点，不必改本文件）。

    必填：code / currency / exponent。能力三项给默认（保守值）：
    不自动付款、不可退款、余额不可见 —— 想 claiming 更强的能力必须显式声明。
    """
    code = (definition or {}).get("code") or ""
    if not code:
        raise ValueError("媒介缺少 code")
    if "currency" not in definition or "exponent" not in definition:
        raise ValueError(f"媒介 {code} 缺少 currency 或 exponent")
    merged = {"label": code, "network": "", "auto_pay": False,
              "refundable": False, "balance_visible": False,
              "region_note": "", **definition}
    MEDIA[code] = merged
    return merged


def get_medium(code: str) -> dict | None:
    return MEDIA.get(code or "")


def medium_of(code: str) -> dict:
    """取媒介定义，未注册直接抛错 —— 静默回退等于默认替业务做了资金决策。"""
    m = get_medium(code)
    if not m:
        raise UnknownMedium(f"未注册的媒介：{code}（已注册：{sorted(MEDIA)}）")
    return m


def currency_of(code: str) -> str:
    return medium_of(code)["currency"]


def exponent_of(code: str) -> int:
    return int(medium_of(code)["exponent"])


def supports(code: str, capability: str) -> bool:
    """该媒介是否具备某项能力（auto_pay / refundable / balance_visible）。"""
    return bool(medium_of(code).get(capability))


def list_media() -> list[dict]:
    return [dict(v) for v in MEDIA.values()]


def by_currency(currency: str) -> list[dict]:
    """按币种反查可用媒介（同一币种可能有多个渠道）。"""
    cur = (currency or "").upper()
    return [dict(v) for v in MEDIA.values() if v["currency"].upper() == cur]


def money(amount_minor: int, code: str) -> dict:
    """把最小单位整数渲染成可读金额（展示用，不参与计算）。"""
    exp = exponent_of(code)
    m = medium_of(code)
    return {"amount_minor": int(amount_minor), "currency": m["currency"],
            "exponent": exp, "medium": code,
            "display": f"{int(amount_minor) / (10 ** exp):.{exp}f} {m['currency']}"}


def to_minor(amount_major: float, code: str) -> int:
    """主单位 → 最小单位整数（唯一入口，杜绝各处自己乘 100）。"""
    return int(round(float(amount_major) * (10 ** exponent_of(code))))


def same_currency(a: str, b: str) -> bool:
    """两种媒介是否同一币种（跨币种不允许成交，见设计 D2）。"""
    try:
        return currency_of(a).upper() == currency_of(b).upper()
    except UnknownMedium:
        return False
