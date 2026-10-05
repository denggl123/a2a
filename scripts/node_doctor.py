"""A2N 节点自检（doctor）—— 面向"陌生人装上就能用"那张清单的最小切片。

装完节点跑不起来、或跑起来却"发现不到别人"，绝大多数原因不在代码，而在环境。
这个脚本把那些环境原因**逐条查出来、并给出可照做的处置**，而不是让人对着
一屏堆栈猜。它只读不写，不改动任何配置。

查这些：

  1. 依赖：`a2n_*` 包是否都装上了（根 pyproject 不含它们，漏装 `python scripts/bootstrap.py`
     是最常见的第一道坑）。
  2. 节点库：`--home` 目录能不能打开、身份读不读得出来。
  3. 端口：节点 HTTP / 反代 / P2P 端口是否被占。
  4. 出网：本机代理设置是不是"会话级/已失效"的代理（会导致跨网络那一跳时通时断）。
  5. P2P：本机是不是**代理模式的 VPN（无 TUN）** —— 那种网络下原生 UDP 出得去、
     回包丢，表现为"对方验签过了却 reachable:false"。这不是代码问题，是网络形状。
  6. 目录源（可选 `--source`）：远端公开入口是不是 HTTPS、能不能拉到目录。

用法
----
  python scripts/node_doctor.py
  python scripts/node_doctor.py --home data/local-node --source https://<远端公开入口>
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

OK, WARN, BAD = "OK", "警告", "问题"
_ROWS: list[tuple[str, str, str]] = []


def row(tag: str, step: str, detail: str) -> None:
    _ROWS.append((tag, step, detail))
    print(f"  [{tag}] {step}：{detail}", flush=True)


def head(t: str) -> None:
    print()
    print(f"──── {t} " + "─" * max(0, 60 - len(t)), flush=True)


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) != 0


def check_deps() -> None:
    head("① 依赖：23 个本地包是否都在")
    missing = []
    for pkg in ("a2n_node", "a2n_sdk", "a2n_p2p", "a2n_acceptance", "a2n_kernel"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        row(BAD, "依赖", f"缺 {missing} —— 先跑 `python scripts/bootstrap.py`（一键装；根 pyproject 不含 a2n-*）")
    else:
        row(OK, "依赖", "a2n_node / a2n_sdk / a2n_p2p / a2n_acceptance / a2n_kernel 都在")


def check_home(home: Path) -> None:
    head("② 节点库：目录与身份")
    if not home.exists():
        row(WARN, "节点库", f"{home} 还不存在（首次启动会建；这是正常的）")
        return
    db = home / "runtime.db"
    if not db.exists():
        row(WARN, "节点库", f"{home} 里还没有 runtime.db（首次启动会建）")
        return
    try:
        from a2n_sdk.storage import LocalStore  # noqa: F401
        row(OK, "节点库", f"runtime.db 在（{db.stat().st_size} 字节）")
    except Exception as exc:  # noqa: BLE001
        row(BAD, "节点库", f"打不开 {db}：{exc}")


def check_ports(node: int, proxy: int, p2p: int) -> None:
    head("③ 端口占用")
    for label, p in (("节点 HTTP", node), ("反代", proxy), ("P2P UDP", p2p)):
        free = port_free("127.0.0.1", p)
        row(OK if free else WARN, label,
            f"{p} {'空闲' if free else '已被占用（若正是本节点在跑，这是正常的）'}")


def check_proxy() -> None:
    head("④ 代理设置：是不是「会话级 / 已失效」的代理")
    keys = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY")
    seen = {k: os.environ[k] for k in keys if os.environ.get(k)}
    if not seen:
        row(OK, "代理", "没有设 http(s)_proxy —— 节点会直连出网")
        return
    dead = []
    for url in set(seen.values()):
        host = urlparse(url).hostname
        port = urlparse(url).port or 80
        if host and port and not port_free(host, port):
            continue  # 端口有人听 = 代理活着
        dead.append(url)
    if dead:
        row(BAD, "代理", f"这些代理连不上：{sorted(set(dead))} —— 节点出网会卡在这里；"
                          f"换一个能用的（或 unset 掉）再起节点")
    else:
        row(OK, "代理", f"代理端口有人听：{sorted(set(seen.values()))}")


def check_p2p_shape() -> None:
    head("⑤ P2P 网络形状：代理模式 VPN 会让原生 UDP 回包丢")
    row(WARN, "P2P", "本节只能给形状判断，不能替网络下结论：")
    print("        - 若对端日志里出现 {verified_envelope_key:true, reachable:false}，"
          "代表 HELLO 与签名都验过了\n          （出站到了），但**回程丢包**；")
    print("        - 根因通常是 VPN 处于**代理模式（无 TUN）**：默认网关仍是本地路由器，"
          "原生 UDP 不过代理；")
    print("        - 处置：把 VPN 切到 **TUN / 全局模式**再试；这是网络形状，不是代码。")
    try:
        import socket as _s
        gw = None
        if sys.platform.startswith("win"):
            gw = None  # 不主动调 netsh/route，避免额外权限与噪声
        if gw:
            print(f"        默认网关：{gw}")
    except Exception:  # noqa: BLE001
        pass


def check_source(source: str | None) -> None:
    head("⑥ 远端目录源（可选）")
    if not source:
        row(WARN, "目录源", "没给 --source，跳过（要给就传远端节点的 HTTPS 公开入口）")
        return
    host = (urlparse(source).hostname or "").lower()
    if source.startswith("http://") and host not in {"127.0.0.1", "localhost"} \
            and not host.endswith(".local"):
        row(BAD, "目录源", "公网地址必须是 HTTPS（明文 http 会被 public_directory 拒收）")
        return
    try:
        import urllib.request
        req = urllib.request.Request(f"{source.rstrip('/')}/public/v1/agents?skill=ping&limit=1",
                                     headers={"User-Agent": "a2n-doctor"})
        with urllib.request.urlopen(req, timeout=12) as r:  # noqa: S310
            body = r.read(400).decode("utf-8", "replace")
        row(OK, "目录源", f"HTTP {r.status}（目录可达） {body[:120]}")
    except Exception as exc:  # noqa: BLE001
        row(WARN, "目录源", f"拉不到目录：{exc}（远端没开公益开关？地址过期？本机没代理？）")


def main() -> int:
    ap = argparse.ArgumentParser(description="A2N 节点自检（只读）")
    ap.add_argument("--home", default="data/local-node", help="节点目录（默认 data/local-node）")
    ap.add_argument("--node-port", type=int, default=8890)
    ap.add_argument("--proxy-port", type=int, default=8891)
    ap.add_argument("--p2p-port", type=int, default=9788)
    ap.add_argument("--source", default=None, help="远端公开入口（可选，查目录可达性）")
    args = ap.parse_args()

    print("=== A2N 节点自检 ===")
    check_deps()
    check_home(Path(args.home))
    check_ports(args.node_port, args.proxy_port, args.p2p_port)
    check_proxy()
    check_p2p_shape()
    check_source(args.source)

    head("结论")
    bad = [r for r in _ROWS if r[0] == BAD]
    warn = [r for r in _ROWS if r[0] == WARN]
    for tag, step, _ in _ROWS:
        if tag != OK:
            print(f"  [{tag}] {step}")
    if bad:
        print(f"\n  ✗ 有 {len(bad)} 个必须先解决的问题（见上）。")
        return 1
    if warn:
        print(f"\n  ⚠ {len(warn)} 条提醒；没有硬问题，节点可以起。")
        return 0
    print("\n  ✓ 全部通过。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
