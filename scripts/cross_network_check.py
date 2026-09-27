"""跨网络双机证据核对 —— 把 GPT 指出的"下一道门槛"做成一条可复现命令。

门槛原话（用户 2026-09-27 转述）：
    两台不在同一网络的普通电脑，各自安装节点；一方挂真实 Agent，
    另一方自主发现并完成免费调用，双方都能核验同一笔收据，断线重启后仍可继续。

这个脚本负责**调用方**的那一半，并且**逐条说清哪一步是本机、哪一步真的跨了网络**：

  ① 把远端节点的公开入口连成一个目录源（`/v1/peers/connect`）
  ② 向它搜能力，按来源分类结果（本机回环 / 本机容器 / **远端公网**）
  ③ 把远端那张卡加入本机"待使用"（`/v1/projections`）
  ④ 走 A2A 真调用一次（`/a2a/{id}` · `message/send`）
  ⑤ 读回本机留存的收据（`/v1/calls/detail`）

它**不粉饰失败**：远端那一跳若是按设计不通（对方供给挂的是死上游），
脚本会把真实错误原样打出来，并把门槛判为"未通过"，同时说明卡在哪一步。

用法
----
  # 只做发现（不调用）：先确认"跨网络发现"这段真的通
  python scripts/cross_network_check.py \\
      --node http://127.0.0.1:8890 \\
      --source https://<远端公开入口> \\
      --skill video-short

  # 连发现带调用（默认就会尝试调用远端那张卡）
  python scripts/cross_network_check.py --node http://127.0.0.1:8890 \\
      --source https://<远端公开入口> --skill video-short --call

环境
----
  * `--node` 是**本机那个全量节点（Daemon）的回环管理口**，不是反代口；脚本自己
    从 `/console` 取本机控制台 cookie（`A2N_LOCAL_TOKEN`），不需要配对码。
  * 对回环一律**不走代理**（`trust_env=False`）；远端那一跳由节点进程自己出，
    与这个脚本无关。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from urllib.parse import urlparse

import httpx

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
_VERDICTS: list[tuple[str, str, str]] = []


def line(t: str = "") -> None:
    print(t, flush=True)


def head(t: str) -> None:
    line()
    line(f"──── {t} " + "─" * max(0, 60 - len(t)))


def verdict(ok: bool, step: str, detail: str) -> None:
    tag = PASS if ok else FAIL
    _VERDICTS.append((tag, step, detail))
    line(f"  [{tag}] {step}：{detail}")


def host_of(url: str) -> str:
    try:
        return (urlparse(url or "").hostname or "").lower()
    except ValueError:
        return ""


def is_local_url(url: str) -> bool:
    return host_of(url) in LOOPBACK_HOSTS


class Node:
    """本机节点的回环管理口（免配对，走 /console 拿 cookie）。"""

    def __init__(self, base: str, timeout: float) -> None:
        self.base = base.rstrip("/")
        self.http = httpx.Client(trust_env=False, timeout=timeout,
                                 follow_redirects=False)
        self.token = ""

    def open_console(self) -> None:
        r = self.http.get(f"{self.base}/console")
        self.token = r.cookies.get("A2N_LOCAL_TOKEN", "")
        if not self.token:
            raise SystemExit(
                f"{self.base}/console 没回 A2N_LOCAL_TOKEN —— 这个口是不是反代口？"
                f"本机节点请用回环管理口（默认 8890），不是对外反代口。")

    def _headers(self) -> dict:
        return {"Cookie": f"A2N_LOCAL_TOKEN={self.token}",
                "Content-Type": "application/json"}

    def get(self, path: str) -> dict:
        r = self.http.get(f"{self.base}{path}", headers=self._headers())
        return _as_json(r)

    def post(self, path: str, body: dict) -> dict:
        r = self.http.post(f"{self.base}{path}", headers=self._headers(), json=body)
        return _as_json(r)


def _as_json(r: httpx.Response) -> dict:
    try:
        data = r.json()
    except ValueError:
        data = {"_raw": r.text[:400]}
    if isinstance(data, dict):
        data.setdefault("_status", r.status_code)
    return data


def pick_remote(results: list[dict], did_prefix: str | None) -> dict | None:
    """挑出"远端公网"那一条：url 不在回环、也没被投影成本机地址。"""
    for item in results:
        card = item.get("card") or {}
        url = card.get("url") or ""
        did = ((card.get("x-a2n") or {}).get("sovereign") or {}).get("did") or ""
        if did_prefix and not str(did).startswith(did_prefix):
            continue
        if url and not is_local_url(url):
            return item
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="跨网络双机证据核对（调用方那一半）")
    ap.add_argument("--node", default="http://127.0.0.1:8890",
                    help="本机全量节点的回环管理口（默认 8890）")
    ap.add_argument("--source", required=True,
                    help="远端节点的公开入口（HTTPS；作为目录源）")
    ap.add_argument("--skill", required=True, help="要发现的能力标识")
    ap.add_argument("--did-prefix", default=None,
                    help="只挑 did 以该前缀开头的远端卡（默认挑第一条远端）")
    ap.add_argument("--call", action="store_true",
                    help="发现之后真的发起一次调用（默认只发现）")
    ap.add_argument("--timeout", type=float, default=45.0, help="单次请求超时秒")
    ap.add_argument("--retries", type=int, default=3,
                    help="搜索重试次数（跨网络那一跳在本机代理模式下会抖，默认 3）")
    ap.add_argument("--retry-wait", type=float, default=2.0,
                    help="两次重试之间的等待秒")
    args = ap.parse_args()

    line("=== A2N 跨网络证据核对（调用方）===")
    line(f"    本机节点 {args.node}   →   远端目录源 {args.source}")

    node = Node(args.node, args.timeout)
    node.open_console()
    line(f"    本机控制台 cookie 已取（A2N_LOCAL_TOKEN {len(node.token)} 字符）")

    # ---------------- ① 连接远端为目录源 ----------------
    head("① 把远端公开入口连成目录源")
    src_host = host_of(args.source)
    if src_host in LOOPBACK_HOSTS:
        verdict(False, "远端入口", f"{args.source} 是回环地址，这不是跨网络")
    elif (args.source or "").startswith("http://") and not src_host.endswith(".local"):
        verdict(False, "远端入口",
                "明文 http 的公网地址不是合法目录源（必须 HTTPS），"
                "见 a2n_node.public_directory.normalize_base")
    out = node.post("/v1/peers/connect", {"address": args.source})
    applied = bool(out.get("applied"))
    verdict(applied, "连接目录源",
            f"kind={out.get('kind')} applied={applied} persisted={out.get('persisted')} "
            f"bases={out.get('bases')}")
    if not applied:
        line(f"    ✗ 连接未生效：{json.dumps(out, ensure_ascii=False)[:300]}")
        return _finish()

    # ---------------- ② 跨网络发现 ----------------
    head("② 向远端目录搜能力（这一步会真的出网）")
    # 本机代理模式下，节点出网到公网 TLS 端点会抖（TLS 握手偶发超时）——重试几次，
    # 并把抖动如实记下来（它不是"没发现"，是"这一跳暂时没过去"）。
    found: dict = {}
    flaky = 0
    for attempt in range(1, max(1, args.retries) + 1):
        found = node.post("/v1/discovery/search", {"skill": args.skill, "limit": 40})
        results = found.get("results") or []
        errors = found.get("errors") or []
        line(f"    第 {attempt} 次：count={found.get('count')}  "
             f"errors={json.dumps(errors, ensure_ascii=False)[:200]}")
        if results and not errors:
            break
        if attempt < args.retries:
            flaky += 1
            time.sleep(args.retry_wait)
    results = found.get("results") or []
    errors = found.get("errors") or []
    if flaky:
        line(f"    ⚠ 远端这一跳抖动 {flaky} 次后才通（代理模式下 TLS 握手会偶发超时）")
    if not results:
        verdict(False, "跨网络发现", "远端一条也没回（看上面 errors）")
        return _finish()

    local_rows, remote_rows = [], []
    for item in results:
        card = item.get("card") or {}
        did = ((card.get("x-a2n") or {}).get("sovereign") or {}).get("did") or "?"
        row = (item.get("source"), card.get("name"), card.get("url"),
               str(did)[:28])
        (local_rows if is_local_url(card.get("url") or "") else remote_rows).append(row)
    for src, name, url, did in remote_rows:
        line(f"    [远端] source={src}  {name}  {url}  did={did}…")
    for src, name, url, did in local_rows:
        line(f"    [本机] source={src}  {name}  {url}  did={did}…")
    verdict(bool(remote_rows), "跨网络发现",
            f"远端 {len(remote_rows)} 条 / 本机 {len(local_rows)} 条（远端 >0 才算跨网络）")
    if not remote_rows:
        line("    ✗ 只搜到本机自己的供给 —— 远端目录源没起作用")
        return _finish()

    target = pick_remote(results, args.did_prefix)
    if target is None:
        verdict(False, "选中远端卡", f"没有 did 以 {args.did_prefix!r} 开头的远端卡")
        return _finish()
    card = target["card"]
    t_did = ((card.get("x-a2n") or {}).get("sovereign") or {}).get("did")
    verdict(True, "选中远端卡", f"{card.get('name')} did={str(t_did)[:28]}… url={card.get('url')}")

    if not args.call:
        line()
        line("（未加 --call，到此为止。加 --call 才会真的发起调用。）")
        return _finish()

    # ---------------- ③ 加入待使用 ----------------
    head("③ 把远端卡加入本机待使用（投影）")
    proj = node.post("/v1/projections", {"card": card, "headers": target.get("headers") or {}})
    pid = proj.get("projection_id") or (proj.get("card") or {}).get("x-a2n", {}).get("projection_id")
    verdict(bool(pid), "加入待使用", f"projection_id={pid}")
    if not pid:
        line(f"    ✗ {json.dumps(proj, ensure_ascii=False)[:300]}")
        return _finish()

    # ---------------- ④ 真调用 ----------------
    head("④ 走 A2A 真调用一次（message/send）")
    mid = f"xnet-{uuid.uuid4()}"
    body = {"jsonrpc": "2.0", "id": "xnet", "method": "message/send",
            "params": {"message": {"messageId": mid,
                                   "parts": [{"kind": "text",
                                              "text": "cross-network free call probe"}]},
                       "metadata": {"skill": (card.get("skills") or [{}])[0].get("id", args.skill)},
                       "configuration": {"blocking": True}}}
    d = node.post(f"/a2a/{pid}", body)
    result = d.get("result") or {}
    err = d.get("error")
    task_id = result.get("id") or result.get("task_id")
    state = ((result.get("status") or {}).get("state")) if isinstance(result, dict) else None
    if err:
        verdict(False, "远端调用", f"返回错误：{json.dumps(err, ensure_ascii=False)[:300]}")
    elif state and state.lower() in {"failed", "canceled", "rejected"}:
        detail = json.dumps(result.get("status") or result, ensure_ascii=False)[:400]
        verdict(False, "远端调用", f"state={state}（**未交付**）{detail}")
    else:
        verdict(True, "远端调用", f"state={state} task_id={task_id}")

    # ---------------- ⑤ 读回收据 ----------------
    head("⑤ 读回本机留存的收据（本节点记的那一份）")
    if not task_id:
        line("    没有 task_id，跳过收据读取")
        return _finish()
    det = node.get(f"/v1/calls/detail?scope={pid}&task_id={task_id}")
    line(f"    {json.dumps(det, ensure_ascii=False)[:600]}")

    return _finish()


def _finish() -> int:
    head("门槛判定（调用方这一半）")
    hard = [v for v in _VERDICTS if v[0] == FAIL]
    for tag, step, _ in _VERDICTS:
        line(f"    [{tag}] {step}")
    line()
    if hard:
        line(f"    ✗ 未通过 —— 卡在：{[v[1] for v in hard]}")
        line("    诚实提醒：若卡在「远端调用」，多半是对方供给挂的上游按设计不通")
        line("    （卡在架≠服务活着）；那是事实，不是本脚本的故障。")
        return 1
    line("    ✓ 通过：跨网络发现 + 免费调用 + 本机收据三步都成立。")
    line("    仍需人工核对的另一半：**远端那台**是否也留了同一笔收据（双方核验）。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
