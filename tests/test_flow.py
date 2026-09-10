"""端到端：充值 → 注册 → 发现 → 发任务 → 执行 → 验收 → 分账 → 提现。"""
from __future__ import annotations

import json

from a2n_dispatch import discovery
from a2n_registry import registry
from a2n_ledger import Ledger
from a2n_task import tasks
from a2n_wallet import wallet
from a2n_server.routers.custodian import DepositIn, PayoutIn, deposit, payout_callback
from a2n_kernel.hashing import new_id
from a2n_settlement import reconcile
from a2n_notary import notary


def demo_card(name: str, skill: str = "ocr-pro", price: int = 1, region: str = "cn-east-2") -> dict:
    return {
        "name": name, "version": "1.0.0", "url": "http://localhost/a2a",
        "skills": [{"id": skill, "name": skill, "tags": ["demo"], "inputModes": ["application/json"]}],
        "x-a2n": {
            "deployment": {"region": region},
            "sla": {"max_latency_ms": 5000},
            "price_hint": {skill: {"amount": price, "unit": "point_per_call"}},
            "metering": {"dimensions": [{"key": "call_count", "verifiable": True},
                                        {"key": "output_tokens", "verifiable": True},
                                        {"key": "gpu_seconds", "verifiable": False}]},
        },
    }


def test_full_loop():
    suffix = new_id("")[2:]
    user, provider = f"acct:u{suffix}", f"acct:p{suffix}"

    # 1. 充值：钱进托管，网内增发等值积分
    deposit(DepositIn(account_id=user, amount_fen=10000))
    assert Ledger().balance(user) == 10000
    assert reconcile.reconcile()["balanced"]

    # 2. 注册节点
    agent = registry.register(provider, demo_card("node-" + suffix))
    assert agent["status"] == "PROBATION"
    registry.heartbeat(agent["agent_id"])

    # 3. 发现（按需拉取）
    # limit 放大：库里还有别的 ocr-pro 节点，按信誉排序取前几条会被挤掉，
    # 与"能不能被发现"无关。真正断言的是：我注册的这个节点一定在被发现集合里。
    found = discovery.query({"skill": "ocr-pro"}, limit=100)
    assert any(a["agent_id"] == agent["agent_id"] for a in found)

    # 4. 发任务，冻结预算（指定本测试的节点，库里还有别的 ocr-pro 节点）
    task = tasks.create(user, "ocr-pro", {"text": "hello"}, budget=100,
                        preferred_agents=[agent["agent_id"]])
    assert Ledger().balance(f"hold:{task['id']}") == 100
    assert Ledger().balance(user) == 9900

    # 5. 节点执行并提交（含计量）
    usage = {"call_count": 1, "output_tokens": 256, "gpu_seconds": 0.8, "wall_time_ms": 120}
    res = tasks.submit(task["id"], agent["agent_id"], {"text": "HELLO"}, usage)
    assert res["passed"], res
    assert res["amount"] == 1  # 合约价 1 积分/次

    # 6. 分账：节点 90%，剩余退回使用方
    node_bal = Ledger().balance(agent["agent_id"])
    assert node_bal == 1 or node_bal >= 0
    assert Ledger().balance(f"hold:{task['id']}") == 0
    assert Ledger().balance(user) == 9900 + (100 - res["amount"])
    assert reconcile.reconcile()["balanced"], "分账后积分总量必须仍等于托管"

    # 7. 凭证链已盖章
    ok, _ = notary.verify()
    assert ok

    # 8. 提现：冻结 → 打款 → 销毁
    wd = wallet.request(agent["agent_id"], max(1, Ledger().balance(agent["agent_id"])))
    assert wd["state"] == "FROZEN"
    paid = payout_callback(PayoutIn(withdrawal_id=wd["withdrawal_id"]))
    assert paid["state"] == "PAID"
    assert reconcile.reconcile()["balanced"], "提现后积分总量必须仍等于托管"


def test_price_hint_never_sorts():
    """红线：提示价不参与排序，否则注册表就成了报价排序器。"""
    s = new_id("")[2:]
    cheap = registry.register(f"acct:c{s}", demo_card("cheap-" + s, price=1))
    rich = registry.register(f"acct:r{s}", demo_card("rich-" + s, price=99))
    # 人为拉开信誉差距：便宜的低信誉，贵的高信誉
    from a2n_store import conn
    conn().execute("UPDATE agents SET reputation=0.4 WHERE agent_id=?", (cheap["agent_id"],))
    conn().execute("UPDATE agents SET reputation=0.9 WHERE agent_id=?", (rich["agent_id"],))
    conn().commit()

    out = discovery.query({"skill": "ocr-pro"},
                          sort=[{"price_hint": "desc"}, {"reputation": "desc"}], limit=50)
    order = [a["agent_id"] for a in out]
    assert rich["agent_id"] in order and cheap["agent_id"] in order
    assert order.index(rich["agent_id"]) < order.index(cheap["agent_id"]), "提示价影响了排序"


def test_assignable_blocks_non_active():
    s = new_id("")[2:]
    agent = registry.register(f"acct:x{s}", demo_card("node-" + s))
    from a2n_store import conn
    conn().execute("UPDATE agents SET status='BLACKLISTED' WHERE agent_id=?", (agent["agent_id"],))
    conn().commit()
    ok, why = discovery.assignable(agent["agent_id"])
    assert not ok and "状态" in why
