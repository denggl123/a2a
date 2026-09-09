"""P2P 网络层演示：三个节点，没有中心服务器。

    A ── B ── C(ocr-pro)

A 只认识 B，C 只认识 B。**A 从未直接连过 C，却能发现它。**
这就是"注册只是允许被发现、发现是按需的、没有人同步全量目录"的底层实现。

    .venv/Scripts/python scripts/p2p_demo.py
"""
from __future__ import annotations

import time

from a2n_p2p import Identity, P2PNode


def line(t: str = "") -> None:
    print(t, flush=True)


def main() -> None:
    line("=== A2N 网络层（L0）演示：无中心服务器的按需发现 ===")
    line()

    # ---- C：提供者，只认识 B ----
    c = P2PNode(Identity.generate(), port=9803, beacon=False)
    c.skills = ["ocr-pro", "translation-v2"]
    c.start()
    line(f"[C] 上线 {c.identity.did}")
    line(f"    提供的能力: {c.skills}")

    # ---- B：中间节点，认识 C ----
    b = P2PNode(Identity.generate(), port=9802, beacon=False,
                bootstrap=[("127.0.0.1", 9803)])
    b.start()
    line(f"[B] 上线 {b.identity.did}  (bootstrap → C)")

    # ---- A：使用方，只认识 B ----
    a = P2PNode(Identity.generate(), port=9801, beacon=False,
                bootstrap=[("127.0.0.1", 9802)])
    a.start()
    line(f"[A] 上线 {a.identity.did}  (bootstrap → B)")
    line()
    time.sleep(0.8)   # 等握手完成

    for name, n in (("A", a), ("B", b), ("C", c)):
        peers = [p.did[:20] for p in n.table.alive()]
        line(f"    {name} 认识 {len(peers)} 个节点: {peers}")
    line()

    # ---- 关键一步：A 询问全网"谁会 ocr-pro" ----
    line("--- A 发出按需查询：谁会 ocr-pro？（A 并不认识 C）---")
    offers = a.query("ocr-pro", timeout=1.5)
    if offers:
        for o in offers:
            endorsed = a.table.is_endorsed(o["did"])
            line(f"    ✓ 找到 {o['did'][:24]}")
            line(f"      能力    {o.get('skills')}")
            line(f"      被担保  {endorsed}   ← TOFU 学到的公钥不等于被担保，"
                 f"信任由上层信誉裁定")
    else:
        line("    ✗ 没有找到（检查端口是否被占用）")
    line()

    # ---- 反向：查一个没人会的能力 ----
    line("--- A 查询一个没人会的能力：quantum-brew ---")
    none = a.query("quantum-brew", timeout=0.8)
    line(f"    结果 {none}  ← 没有就是没有，不会被推荐别的东西"
         f"（平台提供搜索，不提供推荐）")
    line()

    # ---- 统计 ----
    for name, n in (("A", a), ("B", b), ("C", c)):
        line(f"    {name} 报文统计: {n.stats}")

    line()
    line("=== 要点 ===")
    line("  1. 全程没有中心服务器：身份是公钥，发现靠邻居，应答自带回信地址")
    line("  2. A 从未连过 C，靠多跳查询发现它 —— 你发现的网络大小取决于你连了多少人")
    line("  3. 每条消息都验签：网络层不传递无法验证来源的东西")

    for n in (a, b, c):
        n.stop()


if __name__ == "__main__":
    main()
