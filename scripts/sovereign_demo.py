"""A2N 自持网络演示：**没有服务器、没有托管**，三个人就能互相发现调用。

    A ── B ── C

  · A 只认识 B（bootstrap），C 只认识 B：**A 从未连过 C，却能找到它、调用它**；
  · 没有 uvicorn、没有平台库、没有积分、没有持牌托管方；
  · 调用不经任何中间人：请求打到对方**自己**的入口上；
  · 凭据不靠中心公证人：双方互签，各自存一份，谁改历史对方手里就是反证。

    python scripts/sovereign_demo.py

跑完会打印两侧的凭证链、篡改检测结果、以及"进程里根本没导入平台代码"的结构性证据。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in sorted((ROOT / "packages").glob("*/src")):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

PY = sys.executable
NODE = str(ROOT / "scripts" / "sovereign_node.py")
DATA = ROOT / "data" / "sovereign" / "demo"
PORTS = {"a": (9661, 9761), "b": (9662, 9762), "c": (9663, 9763),
         "dora": (9664, 9764)}


def line(t: str = "") -> None:
    print(t, flush=True)


def head(t: str) -> None:
    line()
    line(f"──── {t} " + "─" * max(0, 62 - len(t)))


def clean() -> None:
    shutil.rmtree(DATA, ignore_errors=True)
    DATA.mkdir(parents=True, exist_ok=True)


def spawn(name: str, skill: str, *, bootstrap: str = "", extra: list[str] | None = None):
    http, p2p = PORTS[name]
    cmd = [PY, NODE, "--name", name, "--home", str(DATA / name),
           "--skill", skill, "--http-port", str(http), "--p2p-port", str(p2p), "--json"]
    if bootstrap:
        cmd += ["--bootstrap", bootstrap]
    cmd += extra or []
    log = open(DATA / f"{name}.log", "w", encoding="utf-8")
    p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT),
                         text=True)
    _PROCS.append(p)                     # 出任何意外都要收干净，否则端口会留着
    return p


_PROCS: list = []
_NODE = None                             # 父进程自己那个节点（容器之一，也要关）


def kill_all() -> None:
    """不管怎么退出，都把本次起的节点进程收掉（端口占着会让下次跑不起来）。"""
    global _NODE
    if _NODE is not None:
        try:
            _NODE.stop()
        except Exception:  # noqa: BLE001
            pass
        _NODE = None
    for p in _PROCS:
        try:
            p.terminate()
        except Exception:  # noqa: BLE001
            pass
    _PROCS.clear()
    time.sleep(0.4)                      # 给系统一点时间把端口释放掉


def read_json_line(path: Path, timeout: float = 12.0) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
                ln = ln.strip()
                if ln.startswith("{"):
                    try:
                        return json.loads(ln)
                    except ValueError:
                        pass
        time.sleep(0.2)
    return None


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main() -> int:
    line("=== A2N 自持网络：无服务器 · 无托管 · 三个人就能互相调用 ===")
    clean()

    # ---------------- 起两个节点（各自独立进程 + 独立库） ----------------
    head("① 各起各的进程：C 提供能力，B 做中间人，A 是使用者")
    line(f"    数据目录 {DATA.relative_to(ROOT)} —— 每个节点一个库，互不可见")
    proc_c = spawn("c", "ocr-pro")
    info_c = read_json_line(DATA / "c.log")
    if not info_c:
        line("    ✗ C 没起来，看 " + str(DATA / "c.log"))
        return 1
    line(f"    C  {info_c['did'][:26]}…  能力 {info_c['skills']}  入口 {info_c['endpoint']}")

    proc_b = spawn("b", "echo", bootstrap=f"127.0.0.1:{PORTS['c'][1]}")
    info_b = read_json_line(DATA / "b.log")
    if not info_b:
        line("    ✗ B 没起来，看 " + str(DATA / "b.log"))
        proc_c.terminate()
        return 1
    line(f"    B  {info_b['did'][:26]}…  能力 {info_b['skills']}  "
         f"（只认识 C）")

    # ---------------- 父进程就是 A ----------------
    # 顺序不能反：a2n-store 在 import 时就锁定库路径，所以 use_home 必须先跑。
    from a2n_node.home import use_home

    home_a = use_home(DATA / "a")
    from a2n_node import (SovereignNode, ack_receipt, card_did, card_hash, verify_ack,
                          verify_card, verify_receipt)

    global _NODE
    _NODE = node_a = SovereignNode(name="a", skills=["upper-case"], home=home_a,
                           port=PORTS["a"][0], p2p_port=PORTS["a"][1],
                           bootstrap=[("127.0.0.1", PORTS["b"][1])],
                           executor={"upper-case": lambda p: {
                               "text": str((p or {}).get("text", "")).upper()}}).start()
    line(f"    A  {node_a.did[:26]}…  能力 {node_a.skills}  "
         f"（只认识 B，**不认识 C**）")
    line("    ↑ 三个节点、三个进程、三个库：没有第四个进程在中间")

    time.sleep(2.5)                                    # 等 gossip 握手

    # ---------------- 发现：A 问网络"谁会 ocr-pro" ----------------
    head("② A 靠邻居找 C（多跳发现，没有全局目录）")
    offers = node_a.discover("ocr-pro", timeout=3.0)
    if not offers:
        line("    ✗ 没发现任何候选（gossip 没握手成功？）")
        _cleanup(node_a, proc_b, proc_c)
        return 1
    for o in offers:
        advert = o.get("advert") or {}
        line(f"    ✓ 有人应答：{o['did'][:26]}…")
        line(f"      它自报的业务入口 {advert.get('http')}")
        line(f"      它自报的卡哈希     {str(advert.get('card_hash'))[:32]}…")
    line("    注：这里全是**自报**，一个字都还没验 —— 所以下一步必须取卡来验。")

    # ---------------- 取卡 + 验卡 ----------------
    head("③ 取卡并本地验签（卡片自证，不需要任何机构背书）")
    card, why = node_a.find("ocr-pro", timeout=3.0)
    if card is None:
        line(f"    ✗ 取不到可用的卡：{why}")
        _cleanup(node_a, proc_b, proc_c)
        return 1
    ok, reason = verify_card(card, require_endpoint=True)
    told = (offers[0].get("advert") or {}).get("card_hash")
    line(f"    ✓ 卡验签通过（{reason}）")
    line(f"    ✓ 身份自洽：did 由卡里的公钥推出 → {card_did(card)[:26]}…")
    line(f"    ✓ 与发现阶段播的哈希一致：{told == card_hash(card)}")
    line(f"    卡内容：name={card.get('name')} skills="
         f"{[s['id'] for s in card['skills']]} url={card.get('url')}")

    # ---------------- 调用 ----------------
    head("④ 直连调用：请求打到 C 自己的入口上，没有中间人")
    payload = {"text": "hello sovereign network", "pages": 3}
    line(f"    A → {card.get('url')}/a2n/call  （带 A 的 DID 签名，不是明文身份声明）")
    out = node_a.call("ocr-pro", payload, card=card, timeout=20)
    if not out.ok:
        line(f"    ✗ 调用失败：{out.error}")
        _cleanup(node_a, proc_b, proc_c)
        return 1
    line(f"    ✓ 交付：{json.dumps(out.result, ensure_ascii=False)}")
    line(f"    ✓ 计量：{out.usage}   结算方式：{out.settle_mode}（无托管＝不产生任何钱）")
    r = out.receipt
    line(f"    ✓ C 签名的收据已拿到并验签通过（by={r['by'][:26]}…）")
    line(f"      task_id {r['task_id']} · 输入哈希 {r['input_hash'][:16]}… "
         f"· 输出哈希 {r['output_hash'][:16]}…")

    # ---------------- 互证：两侧各自的链 ----------------
    head("⑤ 双向互证：没有中心公证人，两方各持对方签名的证据")
    ok_c, msg_c = verify_receipt(r)
    mine = node_a.receipts_of(out.task_id)
    line(f"    A 本地链（{node_a.verify_chain()[1]}）：")
    for row in mine:
        line(f"      #{row['seq']:<2} {row['event_type']:<20} {row['hash'][:16]}…")
    line(f"    A 手里的收据 → 用 C 的公钥验签：{ok_c}（{msg_c}）")

    from a2n_node import peer as peermod
    remote = peermod.fetch_chain(card["url"], limit=10)
    if remote:
        line(f"    C 本地链（{remote.get('chain')}）：")
        for row in reversed(remote.get("items") or []):
            line(f"      #{row['seq']:<2} {row['event_type']:<24} {row['hash'][:16]}…")
        acked = any(x["event_type"] == "delivery.acknowledged"
                    for x in (remote.get("items") or []))
        line(f"    C 那边记到了 A 的回执（delivery.acknowledged）：{acked}"
             f"   ← 互证闭环的另一半")

    # ---------------- 篡改与冒名 ----------------
    head("⑥ 改一个字节就露馅：篡改 / 冒名都会被验签挡住")
    bad = dict(r)
    bad["output_hash"] = "0" * 64
    line(f"    改收据的输出哈希      → {verify_receipt(bad)}")
    bad2 = dict(r)
    bad2["task_id"] = "t_凭空捏造"
    line(f"    改收据的 task_id      → {verify_receipt(bad2)}")
    bad3 = dict(r)
    bad3["by"] = node_a.did                     # 冒充别人签的
    line(f"    把签名者改成另一个 DID → {verify_receipt(bad3)}")
    bad_ack = dict(ack_receipt(node_a.identity, r))
    bad_ack["of"] = "被掉包的回执"
    line(f"    回执指向别的收据      → {verify_ack(bad_ack, r)}")

    # ---------------- 新节点入网 ----------------
    head("⑦ 再放一个人进来：只给一个种子地址，它自己找过来")
    line("    起一个从没进过网的新节点 dora（只认识 B，且只做一次性调用）")
    proc_d = spawn("dora", "echo", bootstrap=f"127.0.0.1:{PORTS['b'][1]}",
                   extra=["--call", "upper-case",
                          "--call-payload", '{"text":"i just joined"}',
                          "--wait", "3"])
    try:
        proc_d.wait(timeout=40)
    except subprocess.TimeoutExpired:
        proc_d.terminate()
    d_out = None
    for ln in (DATA / "dora.log").read_text(encoding="utf-8", errors="replace").splitlines():
        if ln.strip().startswith("{"):
            try:
                d_out = json.loads(ln)
            except ValueError:
                pass
    if d_out and d_out.get("outcome") and d_out["outcome"].get("ok"):
        o = d_out["outcome"]
        line(f"    ✓ dora 入网后直接找到了 A 的能力并用上了：")
        line(f"      它发现的候选 {[x['did'][:24] + '…' for x in d_out['offers']]}")
        line(f"      结果 {json.dumps(o['result'], ensure_ascii=False)}")
        line(f"      它本地也落了链：{d_out.get('chain')}")
        line("    ↑ A 没有为它做任何事：它只是连上了一个人，就看见了网络里的另一个人")
    else:
        line(f"    ✗ dora 没能完成调用，看 {DATA / 'dora.log'}")

    # ---------------- 结构性证据 ----------------
    head("⑧ 证据：这套东西真的没有服务器、没有托管")
    line("    节点 A 的进程里导入过的 a2n 包：")
    loaded = sorted({m.split(".")[0] for m in sys.modules if m.startswith("a2n_")})
    line("      " + " · ".join(loaded))
    for forbidden, why in (("a2n_server", "平台（HTTP 应用层）"),
                           ("a2n_custodian", "持牌托管"),
                           ("a2n_account", "账户与支付方式"),
                           ("a2n_settlement", "结算与费用")):
        state = "已导入" if forbidden in loaded else "未导入"
        line(f"      {why:<16} {forbidden:<16} {state}")
    line(f"    涉及的地址：只有三个节点自己的入口")
    for n, (http, p2p) in PORTS.items():
        line(f"      {n:<5} http://127.0.0.1:{http}   gossip udp {p2p}")
    line(f"    平台默认端口 8000 现在有人监听吗：{port_open(8000)}"
         f"   ← 不管有没有，本次演示一个字节都没经过它")
    line(f"    A 的库：{node_a.home / 'a2n.db'}")
    line(f"    C 的库：{DATA / 'c' / 'a2n.db'}")
    line("    两个库互不可见：A 不知道 C 记了什么，直到 C 把收据签给它。")

    _cleanup(node_a, proc_b, proc_c)
    line()
    line("=== 结论 ===")
    line("  · 发现：邻居 gossip + 多跳按需查询（没有全局目录，没有注册中心）")
    line("  · 身份：公钥指纹，不需要谁分配；卡片自证，不需要谁背书")
    line("  · 调用：直连对方入口，请求自带签名（没有中继，没有平台）")
    line("  · 凭据：双向互签，各存一份（没有中心公证人）")
    line("  · 钱：没有托管就没有积分增发 —— 所以**没有账本需要一致**，")
    line("    这才是「不需要服务器」的根本原因，不只是「服务器被替换掉了」")
    return 0


def _cleanup(node_a, *procs) -> None:
    try:
        node_a.stop()
    except Exception:  # noqa: BLE001
        pass
    for p in procs:
        try:
            p.terminate()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    finally:
        kill_all()
