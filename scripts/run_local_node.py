"""本机节点：**一个节点 = 一把钥匙 = 一个身份**，一次上架四张卡。

为什么要这个脚本（2026-09-20 用户拍板）
---------------------------------------
以前是四个 `run_a2a_node.py` 进程 = 四个"节点"，各用一个人造主体
（`acct:bob` / `acct:carol` / `acct:erin` / `acct:frank`）。那不是网络该有的样子：
用户的原话是「**一个节点就一个身份**」—— 一个供给方一个身份 id，只是为了对外辨识。

所以本机这台机器就是**一个节点**：一把钥匙（`data/keys/local_node.json`），
用这把钥匙签**四张卡**，四张卡挂在**同一个主体**（= 这个节点的 did）名下。
"一个节点能上架几个 agent" —— 想摆几张摆几张，这才是供给方的真实形状。

四张卡还是原来那四档（收费 / 免费 / x402 / 试用中），只是从"四个节点"收成"一个节点"。
本地服务端口沿用 9102-9105（一张卡一个入口，平台经 relay 转发进来）。

控制台开箱身份就是**这个节点的 did**（见 `A2N_CONSOLE_PRINCIPAL` 与 a2n-server 的
`/console`）：你打开控制台，看到的是"我这台机器上架了什么"。

用法：
    python scripts/run_local_node.py                # 常驻，四张卡
    python scripts/run_local_node.py --print-did    # 只确保钥匙存在并打印 did（起平台前用）

环境变量：
    A2N_BASE          平台地址（默认 http://127.0.0.1:18787）
    A2N_KEYFILE       钥匙库（默认 data/keys/local_node.json）
    A2N_PORT_BASE     本地服务起始端口（默认 9102，四档依次 +1）
    A2N_ROLES         只起其中几档（逗号分隔，默认全部）
    A2N_PRINCIPAL     覆盖上架主体（默认 = 节点自己的 did；测试造两个主体时才用）
"""
from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_a2a_node as node_lib  # noqa: E402

from a2n_p2p import Identity  # noqa: E402
from a2n_sdk import Node  # noqa: E402

DEFAULT_KEYFILE = Path(os.environ.get("A2N_KEYFILE")
                       or "data/keys/local_node.json")
PORT_BASE = int(os.environ.get("A2N_PORT_BASE", "9102"))
# 四档档位名（本机节点默认全上）。事实源在 run_a2a_node.PRESETS。
ROLES = node_lib.ROLES
# 上架主体口径（= 节点自己的 did）也在这里转出：本机节点的对外承诺就这两条 ——
# "我是谁"与"我摆哪几张卡"，调用方与测试都从这里读，不各自去摸 run_a2a_node。
principal_for = node_lib.principal_for


def ensure_identity() -> Identity:
    """本机节点的身份。**一节点一份** —— 不是一张卡一份。

    起平台前要先知道它是谁（控制台开箱身份 = 它），而钥匙是第一次跑才生成的，
    所以 sim_start 会先单独调一次 `--print-did`。
    """
    return node_lib.load_identity(DEFAULT_KEYFILE)


def build_all(identity: Identity, roles: list[str]) -> list[dict]:
    """一个身份签四张卡 —— 卡不同，人同一个。"""
    return [node_lib.build(role, identity) for role in roles]


def serve_one(built: dict, identity: Identity, port: int, base_url: str,
              principal: str | None) -> None:
    node = Node(built["card"], built["handlers"],
                principal=node_lib.principal_for(identity, principal),
                base_url=base_url,
                discover_limit=built["seats"] or None,
                attest_fn=built["attest_fn"])
    price_txt = " / ".join(f"{v} {c}/次" for c, v in built["prices"].items()) or "免费"
    print(f"[local-node] {built['name']}（{built['role']} · {price_txt}）"
          f"· 本地服务 {port} · 主体 {node.client.principal}", flush=True)
    node.serve(console=False, local_agent=(port, built["local_api"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print-did", action="store_true",
                        help="只确保钥匙存在并打印 did，然后退出（起平台前读它用）")
    parser.add_argument("--base", default=os.environ.get("A2N_BASE",
                                                         "http://127.0.0.1:18787"))
    parser.add_argument("--principal", default=os.environ.get("A2N_PRINCIPAL"))
    args = parser.parse_args()

    identity = ensure_identity()
    if args.print_did:
        print(identity.did)
        return

    roles = [r.strip() for r in (os.environ.get("A2N_ROLES") or "").split(",") if r.strip()]
    if not roles:
        roles = list(node_lib.ROLES)
    unknown = [r for r in roles if r not in node_lib.PRESETS]
    if unknown:
        raise SystemExit(f"A2N_ROLES 里有不认识的档位 {unknown}，只认 {node_lib.ROLES}")

    print(f"[local-node] 本机节点身份 {identity.did} · 上架 {len(roles)} 张卡 · "
          f"平台 {args.base}", flush=True)
    built = build_all(identity, roles)

    errors: queue.Queue = queue.Queue()

    def worker(b: dict, port: int) -> None:
        try:
            serve_one(b, identity, port, args.base, args.principal)
        except Exception as exc:  # noqa: BLE001 - 起不来要如实报，不静默半死
            errors.put((b["role"], exc))

    for index, b in enumerate(built):
        threading.Thread(target=worker, args=(b, PORT_BASE + index),
                         name="local-" + b["role"], daemon=True).start()
    # 起不来就炸（而不是"主线程活着、节点已经死了"那种半死状态）
    role, exc = errors.get()
    raise RuntimeError(f"本机节点的 {role} 档起不来") from exc


if __name__ == "__main__":
    main()
