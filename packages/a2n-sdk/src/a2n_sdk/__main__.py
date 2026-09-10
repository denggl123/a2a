"""命令行入口：codex / claude code / hermes 这类 shell 型 agent 一条命令上架。

    python -m a2n_sdk shelf --platform http://127.0.0.1:8138 --principal me \
        --skill ocr-pro --region cn-east-2 \
        --price CNY:call_count:3 --accept peer_account --accept x402

    # 已有整卡（导出的/别人给的）——贴文件直接上架：
    python -m a2n_sdk shelf --platform … --principal me --card card.json

    # 改价 / 改部署属地（整卡更新）：
    python -m a2n_sdk update-card --platform … --principal me \
        --agent ag_… --card card.json

    # 看看全网有什么 / 自己的货架：
    python -m a2n_sdk list --platform … [--mine]

输出一律 JSON（机器可读）；出错走 stderr 且退出码非 0。
"""
from __future__ import annotations

import argparse
import json
import sys

from .client import Client
from .shelf import from_card as _from_card
from .shelf import shelf as _shelf
from .shelf import update_card as _update_card


def _client(args: argparse.Namespace) -> Client:
    return Client(args.platform, principal=args.principal)


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
    return c._req("GET", "/v1/registry/agents")


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

    l = sub.add_parser("list", help="列出 agent（默认全网，--mine 只看自己）")
    l.add_argument("--mine", action="store_true")
    l.set_defaults(func=cmd_list)

    g = sub.add_parser("get", help="看一个 agent 的完整卡")
    g.add_argument("--agent", required=True)
    g.set_defaults(func=cmd_get)

    args = p.parse_args(argv)
    try:
        out = args.func(args)
    except Exception as e:   # noqa: BLE001 —— CLI 边界，错误必须可读
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
