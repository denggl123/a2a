"""命令行入口：codex / claude code / hermes 这类 shell 型 agent 一条命令上架。

    python -m a2n_sdk shelf --platform http://127.0.0.1:8138 --principal me \
        --skill ocr-pro --region cn-east-2 \
        --price CNY:call_count:3 --accept peer_account --accept x402

    # 已有整卡（导出的/别人给的）——贴文件直接上架：
    python -m a2n_sdk shelf --platform … --principal me --card card.json

    # 改价 / 改部署属地（整卡更新）：
    python -m a2n_sdk update-card --platform … --principal me \
        --agent ag_… --card card.json

    # 看看全网有什么 / 自己的货架（默认精简表，--json 才吐原始行）：
    python -m a2n_sdk list --platform … [--mine]

    # 发现（按能力/结算方式/属地/预算/信誉筛）与调用（走治理链）：
    python -m a2n_sdk discover --platform … --skill ocr-pro --accept peer_account
    python -m a2n_sdk call --platform … --principal me --agent ag_… \
        --skill ocr-pro --payload '{"text":"hi"}'

输出一律 JSON（机器可读）；出错走 stderr 且退出码非 0
（需要付款=3、没资格=4，便于脚本分流）。
"""
from __future__ import annotations

import argparse
import json
import sys

from .client import Client
from .errors import CallDeniedError, PaymentRequiredError
from .shelf import from_card as _from_card
from .shelf import shelf as _shelf
from .shelf import update_card as _update_card


def _client(args: argparse.Namespace) -> Client:
    return Client(args.platform, principal=args.principal)


_CUR_EXP = {"CNY": 2, "USD": 2, "EUR": 2, "HKD": 2, "JPY": 0, "USDC": 6}


def _money(minor, cur) -> str:
    """最小单位整数 → 主单位可读串（3 CNY → 0.03 CNY，50000 USDC → 0.05 USDC）。

    机器要的是最小单位，人看的是主单位；两个都给出，别让人心算 10 的幂。
    """
    e = _CUR_EXP.get(str(cur or "CNY").upper(), 2)
    return f"{round(float(minor or 0) / (10 ** e), e):g} {cur}"


def _first_price(book: dict) -> tuple:
    """价目表 → (币种, 维度 key, 最小单位)。取第一条，与展示口径一致。"""
    for _skill, by_cur in (book or {}).items():
        for cur, spec in (by_cur or {}).items():
            entries = spec.get("dimensions") if isinstance(spec, dict) else spec
            if entries:
                e = entries[0]
                return cur, e.get("key"), e.get("amount")
    return None, None, None


def _price_of_card(card: dict) -> tuple:
    """卡 → (一句话价格, 最小单位, 币种)。价格事实只在 card 里：v2 价目 → v1 提示价 → 免费。"""
    ext = (card or {}).get("x-a2n") or {}
    cur, key, amount = _first_price(ext.get("price_book") or (card or {}).get("price_book"))
    if cur is not None:
        return f"{_money(amount, cur)}/{key}", amount, cur
    hint = ext.get("price_hint") or (card or {}).get("price_hint") or {}
    for _skill, spec in (hint or {}).items():
        if isinstance(spec, dict):       # v1 口径：分/次
            return f"{_money(spec.get('amount'), 'CNY')}/call", spec.get("amount"), "CNY"
    return "免费", None, None


def _price_of_book(book: dict) -> str:
    """发现投影的 price_book → 一句话价格。"""
    cur, key, amount = _first_price(book)
    return "免费" if cur is None else f"{_money(amount, cur)}/{key}"


def _slim_agent(a: dict) -> dict:
    """registry 行 → 精简表：名称/技能/价格/结算方式/信誉/属地/可达。

    默认不吐 `card_json` 整串（那是机器换手的载荷，不是给人看的表）；
    `--json` 才给原始行。价目只从 card 派生 —— 不再出现"列里 null、卡里有值"。
    """
    try:
        card = json.loads(a.get("card_json") or "{}")
    except ValueError:
        card = {}
    accepts = card.get("accepts") or (card.get("x-a2n") or {}).get("accepts") or []
    price, minor, cur = _price_of_card(card)
    return {
        "agent_id": a.get("agent_id"),
        "name": a.get("name"),
        "skills": [s.get("id") for s in (card.get("skills") or []) if s.get("id")],
        "price": price,
        "price_minor": minor,
        "currency": cur,
        "accepts": accepts,
        "reputation": a.get("reputation"),
        "region": (a.get("compute") or {}).get("region"),
        "status": a.get("status"),
    }


def _slim_found(r: dict) -> dict:
    """发现投影 → 精简表（发现行本身已是干净投影，这里只挑买方关心的几列）。"""
    book = r.get("price_book") or {}
    _cur, _key, minor = _first_price(book)
    return {
        "agent_id": r.get("agent_id"),
        "name": r.get("name"),
        "price": _price_of_book(book),
        "price_minor": minor,
        "currencies": r.get("currencies") or [],
        "accepts": r.get("accepts") or [],
        "reputation": r.get("reputation"),
        "region": r.get("region"),
        "reachable": r.get("reachable"),
    }


def _parse_price(spec: str) -> tuple[str, dict]:
    """CNY:call_count:3[:per] → (CNY, {dimensions:[{key,amount,per}]})"""
    parts = spec.split(":")
    if len(parts) not in (3, 4):
        raise ValueError(f"--price 格式：CUR:KEY:AMOUNT[:PER]，收到 {spec!r}")
    cur, key, amount = parts[0], parts[1], int(parts[2])
    per = int(parts[3]) if len(parts) == 4 else 1
    return cur, {"dimensions": [{"key": key, "amount": amount, "per": per}]}


def cmd_shelf(args: argparse.Namespace) -> dict:
    c = _client(args)
    if args.card:
        card = args.card if args.card.strip().startswith("{") else open(args.card, encoding="utf-8").read()
        return _from_card(c, card, args.visibility)
    if not args.skill:
        raise ValueError("要么 --card 整卡上架，要么至少给一条 --skill")
    price: dict = {}
    for spec in args.price or []:
        cur, entry = _parse_price(spec)
        price.setdefault(args.skill[0], {})[cur] = entry   # 简写价挂第一条技能
    deployment = {"region": args.region} if args.region else None
    sla = {"max_latency_ms": args.lat} if args.lat else None
    return _shelf(c, skills=args.skill, name=args.name, desc=args.desc,
                  version=args.version, url=args.url, deployment=deployment,
                  sla=sla, accepts=args.accept, metering=args.dim,
                  price=price or None, uid=args.uid, visibility=args.visibility)


def cmd_update_card(args: argparse.Namespace) -> dict:
    card = args.card if args.card.strip().startswith("{") else open(args.card, encoding="utf-8").read()
    return _update_card(_client(args), args.agent, card)


def cmd_list(args: argparse.Namespace) -> list[dict]:
    c = Client(args.platform)   # 全网视角；--mine 才带 principal
    if args.mine:
        c.principal = args.principal
    rows = c._req("GET", "/v1/registry/agents")
    return rows if args.json else [_slim_agent(a) for a in rows]


def cmd_discover(args: argparse.Namespace) -> list[dict]:
    """按能力找 agent（发现永远开放，不设门槛；筛的是偏好不是资格）。"""
    filt: dict = {}
    if args.accept:
        filt["accepts"] = args.accept
    if args.region:
        filt["region"] = args.region
    if args.max_price is not None:
        filt["max_price_minor"] = args.max_price
    if args.min_reputation is not None:
        filt["min_reputation"] = args.min_reputation
    if args.reachable:
        filt["reachable"] = True
    rows = _client(args).discover(args.skill, filt=filt or None, limit=args.limit)
    return rows if args.json else [_slim_found(r) for r in rows]


def cmd_call(args: argparse.Namespace) -> dict:
    """调用一个 agent（走统一治理链：门禁 → 建任务 → 执行 → 验收 → 记账）。

    绑定了对方接受的直付渠道、或已配对的对等账户，都能直接调；
    免费 agent 零配置；对方接受 x402 且没带 `--payment` 时会以退出码 3 报告挑战体。
    """
    payload = None
    if args.payload:
        raw = args.payload
        if not raw.strip().startswith(("{", "[")):
            raw = open(raw, encoding="utf-8").read()      # 也接受 payload 文件
        payload = json.loads(raw)
    return _client(args).call_agent(args.agent, skill=args.skill or "",
                                    payload=payload, currency=args.currency,
                                    payment=args.payment)


def cmd_get(args: argparse.Namespace) -> dict:
    return _client(args)._req("GET", f"/v1/registry/agents/{args.agent}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m a2n_sdk",
                                description="A2N 货架：把 agent 摆上网络（机器可读 JSON 输出）")
    p.add_argument("--platform", default="http://127.0.0.1:8000", help="平台地址")
    p.add_argument("--principal", default=None, help="主体身份（X-Principal）")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("shelf", help="上架：关键字段模式或 --card 整卡模式")
    s.add_argument("--card", help="整卡 JSON 文件路径或 JSON 字符串（给了就整卡上架）")
    s.add_argument("--skill", action="append", help="技能 id，可重复；简写价挂第一条")
    s.add_argument("--name"); s.add_argument("--desc")
    s.add_argument("--uid", help="网络唯一标识（UUID）；缺省自动生成")
    s.add_argument("--url", default="http://localhost:9000/a2a")
    s.add_argument("--version", default="1.0.0")
    s.add_argument("--region", help="部署属地，如 cn-east-2")
    s.add_argument("--lat", type=int, help="最大延迟 ms")
    s.add_argument("--price", action="append", help="CUR:KEY:AMOUNT[:PER]，如 CNY:call_count:3")
    s.add_argument("--accept", action="append",
                   help="peer_account | direct_pay:渠道 | x402，可重复")
    s.add_argument("--dim", action="append", help="计量维度 key，如 output_tokens，可重复")
    s.add_argument("--visibility", default="public")
    s.set_defaults(func=cmd_shelf)

    u = sub.add_parser("update-card", help="整卡更新（改价/改部署属地；uid 不可变）")
    u.add_argument("--agent", required=True)
    u.add_argument("--card", required=True, help="新卡 JSON 文件路径或 JSON 字符串")
    u.set_defaults(func=cmd_update_card)

    l = sub.add_parser("list", help="列出 agent（默认精简表，--json 给原始行）")
    l.add_argument("--mine", action="store_true")
    l.add_argument("--json", action="store_true", help="输出原始 registry 行")
    l.set_defaults(func=cmd_list)

    d = sub.add_parser("discover", help="按能力发现 agent（可叠加结算方式/属地/预算/信誉筛）")
    d.add_argument("--skill", required=True, help="能力/技能 id")
    d.add_argument("--accept", action="append",
                   help="结算方式偏好：peer_account | direct_pay:渠道 | x402，可重复")
    d.add_argument("--region", help="部署属地，如 cn-east-2")
    d.add_argument("--max-price", type=int, dest="max_price",
                   help="单价上限，最小单位整数（CNY 用分：300=¥3；USDC 用 10⁻⁶）")
    d.add_argument("--min-reputation", type=float, dest="min_reputation",
                   help="信誉下限（0~1）")
    d.add_argument("--reachable", action="store_true", help="只要当前可达的")
    d.add_argument("--limit", type=int, default=20)
    d.add_argument("--json", action="store_true", help="输出原始发现行")
    d.set_defaults(func=cmd_discover)

    cl = sub.add_parser("call", help="调用一个 agent（走治理链）")
    cl.add_argument("--agent", required=True, help="agent_id")
    cl.add_argument("--skill", help="技能 id（缺省用卡上第一个技能）")
    cl.add_argument("--payload", help="参数 JSON 字符串或文件路径")
    cl.add_argument("--currency", help="想用的结算币种（缺省按 agent 价目默认）")
    cl.add_argument("--payment", help="x402 支付凭证（402 挑战后带回来重试）")
    cl.set_defaults(func=cmd_call)

    g = sub.add_parser("get", help="看一个 agent 的完整卡")
    g.add_argument("--agent", required=True)
    g.set_defaults(func=cmd_get)

    args = p.parse_args(argv)
    try:
        out = args.func(args)
    except PaymentRequiredError as e:
        print("payment required: 该调用需要支付（x402）", file=sys.stderr)
        print(json.dumps(e.requirement, ensure_ascii=False, indent=2), file=sys.stderr)
        return 3
    except CallDeniedError as e:
        print(f"调用被拒：{e}", file=sys.stderr)
        if e.hint:
            print(json.dumps(e.hint, ensure_ascii=False, indent=2), file=sys.stderr)
        return 4
    except Exception as e:   # noqa: BLE001 —— CLI 边界，错误必须可读
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
