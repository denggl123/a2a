"""起一个自持节点：自己的钥匙、自己的库、自己的入口 —— 没有平台，没有托管。

    python scripts/sovereign_node.py --name alice --home data/sovereign/alice \\
        --skill upper-case --http-port 9661 --p2p-port 9761 --bootstrap 127.0.0.1:9762

要点：

  · **import 顺序**：先 `a2n_node.home.use_home()` 定库，再 import a2n_node。
    a2n-store 在 import 时就锁定了库路径（见 a2n_node/home.py 的说明）。
  · 每个进程一个节点 —— 想要第二个节点就再起一个进程，不要在同一个进程里塞两个。
  · 内置几个演示用执行体（echo / upper-case / ocr-pro / boom），
    真实用法是把 --skill 换成自己的函数（见本文件末尾的入口）。
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path

# 自己把仓库里的 packages/*/src 接进来，不依赖外部 PYTHONPATH。
# editable 安装在这台机器上有丢过（见项目记忆），而"节点起不来"不该是配环境问题。
_ROOT = Path(__file__).resolve().parents[1]
for _p in sorted((_ROOT / "packages").glob("*/src")):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _executors() -> dict:
    """演示执行体。真实的节点把这里换成自己的推理/工具调用。"""

    def echo(payload):
        p = payload or {}
        return {"echo": p, "worker": "sovereign-node"}

    def upper_case(payload):
        p = payload or {}
        return {"text": str(p.get("text", "")).upper(), "chars": len(str(p.get("text", "")))}

    def ocr_pro(payload):
        p = payload or {}
        time.sleep(0.2)                      # 假装在推理
        return {"text": str(p.get("text", "")).upper(),
                "pages": int(p.get("pages", 1)), "engine": "sovereign-ocr"}

    def boom(payload):
        raise RuntimeError("这个执行体故意失败，用来看失败路径")

    return {"echo": echo, "upper-case": upper_case, "ocr-pro": ocr_pro, "boom": boom}


def main() -> int:
    ap = argparse.ArgumentParser(description="A2N 自持节点（无服务器、无托管）")
    ap.add_argument("--name", required=True)
    ap.add_argument("--home", default="")
    ap.add_argument("--skill", action="append", default=[])
    ap.add_argument("--http-port", type=int, default=0)
    ap.add_argument("--http-host", default="127.0.0.1")
    ap.add_argument("--advertise-host", default="")
    ap.add_argument("--p2p-port", type=int, default=9761)
    ap.add_argument("--bootstrap", action="append", default=[],
                    help="种子节点 host:port（可多次）")
    ap.add_argument("--beacon", action="store_true", help="广播找同机节点")
    ap.add_argument("--restricted", action="append", default=[],
                    help="不对外开放的能力（不在白名单里的 did 调不动）")
    ap.add_argument("--allow", action="append", default=[], help="白名单 did")
    ap.add_argument("--json", action="store_true", help="启动后打印一行 JSON（给父进程读取）")
    ap.add_argument("--call", default="", help="一次性模式：发现这个能力并调用它，打印结果后退出")
    ap.add_argument("--call-payload", default="{}", help="一次性模式的入参（JSON）")
    ap.add_argument("--wait", type=float, default=2.0, help="一次性模式：等邻居握手的时间")
    args = ap.parse_args()

    # Machine-readable output is a wire format.  Keep it UTF-8 on Windows as
    # well, where an inherited console code page would otherwise corrupt node
    # names and delivery-chain diagnostics read by another process.
    if args.json or args.call:
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure:
                reconfigure(encoding="utf-8")

    home = args.home or str(Path("data") / "sovereign" / args.name)

    # ① 先定库（本模块零 a2n 依赖，此刻还没 import 任何 a2n 包）
    from a2n_node.home import use_home
    use_home(home)

    # ② 再 import 节点（这时才碰 a2n-store，库已经定好了）
    from a2n_node import SovereignNode, card_did

    boot = []
    for b in args.bootstrap:
        host, _, port = b.partition(":")
        boot.append((host, int(port or 9761)))

    execs = _executors()
    skills = args.skill or ["echo"]
    unknown = [s for s in skills if s not in execs]
    if unknown:
        print(f"[{args.name}] 不认识的执行体：{unknown}（可选 {sorted(execs)}）", flush=True)
        return 2

    node = SovereignNode(
        name=args.name, skills=skills, home=home,
        host=args.http_host, port=args.http_port,
        advertise_host=args.advertise_host or args.http_host,
        p2p_port=args.p2p_port, bootstrap=boot, beacon=args.beacon,
        executor={s: execs[s] for s in skills},
        open_skills=None if not args.restricted else
        [s for s in skills if s not in set(args.restricted)],
        allow_dids=set(args.allow),
        verbose=not args.json,
    ).start()

    info = {"name": node.name, "did": node.did, "endpoint": node.endpoint,
            "p2p_port": node.p2p.port, "skills": node.skills,
            "home": str(node.home)}

    # ---------- 一次性模式：新节点入网 → 发现 → 调用 → 退出 ----------
    # 这是"只要有人用就能互相发现调用"的最小证明：一个从没进过网的节点，
    # 只拿到一个种子地址，就能找到某个能力并调用它。
    if args.call:
        time.sleep(args.wait)                       # 等邻居握手
        payload = json.loads(args.call_payload or "{}")
        offers = node.discover(args.call, timeout=max(2.0, args.wait))
        card, why = node.find(args.call, timeout=max(2.0, args.wait))
        out = node.call(args.call, payload, card=card) if card else None
        print(json.dumps({
            "me": {"name": node.name, "did": node.did, "endpoint": node.endpoint},
            "offers": [{"did": o.get("did"), "advert": o.get("advert")} for o in offers],
            "card": {"did": card_did(card), "name": (card or {}).get("name"),
                     "url": (card or {}).get("url")} if card else None,
            "card_error": "" if card else why,
            "outcome": out.to_dict() if out else None,
            "chain": node.verify_chain()[1],
        }, ensure_ascii=False, default=str), flush=True)
        node.stop()
        return 0 if (out and out.ok) else 1

    if args.json:
        print(json.dumps(info, ensure_ascii=False), flush=True)
    else:
        print(f"[{args.name}] {json.dumps(info, ensure_ascii=False)}", flush=True)
        print(f"[{args.name}] 无平台进程、无托管；Ctrl+C 退出", flush=True)

    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
    while not stop["flag"]:
        time.sleep(0.2)
    node.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
