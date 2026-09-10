"""Docker 互通演示：主机上的调用方 → 发现容器里的 agent → 配对 → 经门禁调用。

前置：
  1) 主机起平台（必须监听 0.0.0.0，容器才能连进来）
     A2N_DB=data/docker.db uvicorn a2n_server.app:app --host 0.0.0.0 --port 8000
  2) 起几个容器节点（不映射任何端口，全靠出站连接）
     docker run -d --name a2n-ocr --add-host host.docker.internal:host-gateway \
       -e A2N_SKILL=ocr-pro -e A2N_NAME=docker-ocr -e A2N_PRINCIPAL=acct:docker-ocr \
       -e A2N_GPU=4090 -e A2N_REGION=cn-docker a2n-agent
  3) 跑本脚本
"""
from __future__ import annotations

import sys

from a2n_sdk import Client

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ME = "acct:host-demo"
SKILLS = sys.argv[2].split(",") if len(sys.argv) > 2 else ["ocr-pro", "translate", "render"]


def main() -> None:
    c = Client(BASE, principal=ME)
    acc = c.create_account("主机-演示账户", ref="host-demo")
    print(f"账户: {acc['account_id']} ({acc['label']})\n")

    for skill in SKILLS:
        found = c.discover(skill, filt={"accepts": ["peer_account"]}, limit=10)
        print(f"── 能力 {skill}: 找到 {len(found)} 个支持对等账户的节点")
        for a in found:
            print(f"   {a['agent_id']}  {a['name']}  信誉={a['reputation']:.2f} "
                  f"属地={a['deployment'].get('region')} 可达={a['reachable']}")
        if not found:
            continue

        target = found[0]
        lk = c.propose_peer(acc["account_id"], target["agent_id"],
                            terms={"unit_prices": {"call_count": 3}, "net_days": 30},
                            auto_accept=True)
        print(f"   配对: {lk['link_id']} {lk['state']}")

        try:
            out = c.call_agent(target["agent_id"], "invoke", {"hello": "from host"})
            print(f"   调用返回: {out}")
        except Exception as e:  # noqa: BLE001
            print(f"   调用失败: {str(e)[:160]}")
            continue

        # 一期记账：双方各报一次，对账，出账
        deal = c.open_deal(lk["link_id"], skill)
        c.report_deal(deal["deal_id"], "provider", {"call_count": 1})
        c.report_deal(deal["deal_id"], "requester", {"call_count": 1})
        r = c.reconcile_deal(deal["deal_id"])
        st = c.issue_statement(lk["link_id"])
        print(f"   交易 {deal['deal_id']} → 对账 {r['state']} "
              f"{r['recon']['amount_fen']} 分 → 账单 {st['total_fen']} 分\n")


if __name__ == "__main__":
    main()
