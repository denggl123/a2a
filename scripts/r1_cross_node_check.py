"""R1 跨节点验收：**本机节点（买方）→ 容器节点（卖方）** 的真实调用。

验的不是"函数能返回字典"，而是 `docs/VISION.md` §1.2 #12「样品即履历」在**真拓扑**上成立：

  1. 本机节点（第 4 个节点）用**目录发现**拿到容器节点发布的供给卡 —— 卡上**调用前**
     就带着 `x-a2n.trial` 声明（前 N 次免费、交付默认成公开样品）。
  2. 本机节点把该卡导入为本机投影，发起**真实 A2A 调用**（跨节点、走容器公共入口）。
  3. 卖方（容器）在自己的账本里记下"完成次数 + 样品" —— 这一步由容器自己的
     `/v1/runtime` 读出来佐证（见 runbook：`docker exec ... /v1/runtime`）。
  4. 反向也要验：容器上那张**故意留死的**供给，调用失败 → **不占名额、不长样品**。

用法（PYTHONPATH 需包含 packages/*/src）：
  python scripts/r1_cross_node_check.py \
      --host-home data/r1-host-home --host-port 8771 \
      --seller-base http://127.0.0.1:8791 --skill video-short --calls 10
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.request
import uuid

from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector

# 验收专用固定口令：让本机节点目录可被重复打开（不是生产凭据）。
_HOST_KEY = os.environ.get("R1_HOST_KEY") or base64.b64encode(b"a2n-r1-verify-key-32bytes-padded").decode()


def _get(url: str) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=10) as resp:
        return json.loads(resp.read())


def _discover(seller_base: str, skill: str) -> list[dict]:
    data = _get(f"{seller_base}/public/v1/agents?skill={skill}&limit=20")
    return list(data.get("cards") or [])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host-home", default="data/r1-host-home")
    ap.add_argument("--host-port", type=int, default=8771)
    ap.add_argument("--seller-base", default="http://127.0.0.1:8791")
    ap.add_argument("--skill", default="video-short")
    ap.add_argument("--dead-skill", default="video-script")
    ap.add_argument("--calls", type=int, default=10)
    ap.add_argument("--payload", default="",
                    help="调用输入（用 | 代表换行）；留空则用视频脚本的默认样例")
    args = ap.parse_args()

    os.makedirs(args.host_home, exist_ok=True)
    proto = EnvironmentProtector(_HOST_KEY)
    daemon = Daemon(args.host_home, port=args.host_port, protector=proto).start()
    print(f"[buyer] 本机节点已起：{daemon.identity.did} · {daemon.runtime.local_base_url}/console",
          flush=True)
    try:
        # ① 目录发现：卡上调用前就该带试用声明
        cards = _discover(args.seller_base, args.skill)
        assert cards, f"目录里没发现 {args.skill} 的供给（卖方的公共目录没开？）"
        card = cards[0]
        trial = (card.get("x-a2n") or {}).get("trial") or {}
        print(f"[buyer] 发现卖方供给：{card.get('name')} · {card.get('url')}", flush=True)
        print(f"[buyer] 卡上试用声明（调用前可见）：cap={trial.get('cap')} "
              f"ended={trial.get('ended')} policy={trial.get('policy')}", flush=True)
        assert trial.get("cap") == 10, "卡上没有调用前的试用声明"

        # ② 导入为本机投影 → 真实跨节点调用
        item = daemon.runtime.import_agent(card)
        pid = item.projection_id
        print(f"[buyer] 已导入本机投影：{pid}", flush=True)

        from a2n_sdk.ports import CallRequest

        done = 0
        for i in range(args.calls):
            topic = f"第 {i} 单：夏季促销短视频"
            body = args.payload.replace("|", "\n") if args.payload else \
                f"{topic}\n限时五折\n满199减50\n新客立减20"
            req = CallRequest(skill=args.skill, task_id=f"r1_x_{uuid.uuid4().hex}",
                              payload=body)
            out = daemon.runtime.invoke_projection(pid, req)
            # 买方侧收口状态叫 ACCEPTED（已按声明验收交付）；卖方侧才记 COMPLETED。
            if out.ok and out.state.upper() in {"ACCEPTED", "COMPLETED"}:
                done += 1
            print(f"[buyer] 调用 {i + 1}/{args.calls}: ok={out.ok} state={out.state} "
                  f"result={(json.dumps(out.result, ensure_ascii=False)[:80] if out.result else out.error)}",
                  flush=True)
        print(f"[buyer] 交付成功 {done}/{args.calls}", flush=True)

        # ③ 同单重试只算一次（幂等）：同一 task_id 再发一次
        same = f"r1_retry_{uuid.uuid4().hex}"
        for _ in range(2):
            daemon.runtime.invoke_projection(
                pid, CallRequest(skill=args.skill, task_id=same,
                                 payload="重试同一单\n限时五折"))
        print(f"[buyer] 同一 task_id 重试 2 次（卖方应只多计 1 次）", flush=True)

        # ④ 死供给：调用失败 → 卖方不许占名额、不许长样品
        dead_cards = _discover(args.seller_base, args.dead_skill)
        if dead_cards:
            dead = daemon.runtime.import_agent(dead_cards[0])
            out = daemon.runtime.invoke_projection(
                dead.projection_id,
                CallRequest(skill=args.dead_skill, task_id=f"r1_dead_{uuid.uuid4().hex}",
                            payload={"topic": "会失败"}))
            print(f"[buyer] 死供给调用：ok={out.ok} state={out.state} error={str(out.error)[:80]}",
                  flush=True)
        else:
            print(f"[buyer] （目录里没有 {args.dead_skill} 的供给，跳过死卡验证）", flush=True)

        print("[buyer] 买方侧结束。卖方侧账本请用 runbook 读 /v1/runtime（容器内 8787）。", flush=True)
        return 0
    finally:
        daemon.stop()


if __name__ == "__main__":
    sys.exit(main())
