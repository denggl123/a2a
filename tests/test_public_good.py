"""纯公益：网络**不抽任何费用** —— 一次调用结算多少，节点就拿到多少。

发起人 2026-09-18 拍板：
「当成纯公益项目（即便以后有托管商，也不收费，只把不同货币转换为积分，
解决不同账户不互通问题）。目的就是人人都能加入网络，匹配到适合自己的 agent。
上手简单，方便，好用，公平，可靠。」

这条曾经是 90/2/3/5（网络服务费 3% + 冷启动池 5% + 样本作者池 2%，出自立项期
《项目全案设计文档 v1.0》）。它不是被调小，是被**取消**。

而且不许哪天被"顺手加回来"：**默认值与执行路径两道都钉死** ——
改默认值会被这里打红，绕过默认值换一个 PolicyRef 会被 `settle` 的运行时守卫拦下。
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from a2n_kernel.policy import DEFAULT_SPLIT, PolicyRef, split_amount, split_rule
from a2n_ledger import Ledger, ensure_account, list_accounts
from a2n_settlement import settlement
from a2n_store import tx

# 旧版规则（作废）：留着是为了验证"能重放历史单子"，不是为了还能用
LEGACY = PolicyRef(key="split.fixed", version="2026.09.01",
                   params={"node": 0.90, "author": 0.02, "fee": 0.03, "pool": 0.05})


def _u(tag: str) -> str:
    return f"{tag}-{uuid4().hex[:8]}"


# ---------- 默认值 ----------

def test_default_split_pays_the_node_in_full():
    assert set(DEFAULT_SPLIT) == {"node"}, f"默认分账不许有非节点份额：{DEFAULT_SPLIT}"
    assert DEFAULT_SPLIT["node"] == 1.0


def test_rule_version_is_the_effective_date():
    """版本号记的是**规则生效日**：历史单子靠它重放，所以改口径必须换版本。"""
    assert split_rule().version == "2026.09.18"


def test_split_amount_gives_everything_to_the_node():
    # 含除不尽的金额：`split_amount` 是"非节点份额算完、节点拿余数"的写法，
    # 一旦有人往里塞回一个 fee key，这里的等号立刻炸。
    for amount in (1, 3, 7, 100, 9999, 12345):
        parts = split_amount(amount)
        assert parts == {"node": amount}, f"{amount} 应全额归节点，实得 {parts}"
        assert sum(parts.values()) == amount


def test_old_rule_still_replayable():
    """取消抽成 ≠ 不能重放：两年后被问"这笔单为什么这么分"，仍算得出来。"""
    old = split_amount(10000, LEGACY)
    assert old == {"author": 200, "fee": 300, "pool": 500, "node": 9000}
    assert sum(old.values()) == 10000


# ---------- 执行路径 ----------

def test_settlement_pays_the_node_in_full():
    node = _u("node:pg")
    buyer = _u("acct:pg-buyer")
    task_id = _u("t")
    hold = f"hold:{task_id}"
    # 生产路径是"两拍"：`tasks.create` 先把预算冻进 hold:task_id，结算再从它扣款、
    # 把没花掉的差额退回使用方。这里照抄这一步 —— 少了它，settle 全额扣款会把
    # 冻结户扣成负数，那是**测试搭错了台**，不是代码的错。
    #
    # 冻结**必须成对**（使用方 −budget / 冻结户 +budget）：整套测试共用一个库，
    # 只写一半会让"积分总量 ≡ 托管余额"这条恒等式当场不平，并且漏到后面
    # 任何一条调 reconcile() 的用例上去（2026-09-18 真栽过一次）。
    ensure_account(hold, "hold", hold)
    Ledger().post(buyer, -100, "freeze", task_id)
    Ledger().post(hold, 100, "freeze", task_id)

    out = settlement.settle(task_id, buyer, node, 100)
    assert out["splits"] == {"node": 100}, out["splits"]
    assert out["rule_ref"]["version"] == "2026.09.18"

    led = Ledger()
    assert led.balance(node) == 100
    assert led.balance(hold) == 0, "冻结户必须刚好清空"
    # 网络与那几个池子一分钱都没拿到 —— 账户压根没被建出来
    account_ids = {row["id"] for row in list_accounts()}
    for legacy in ("acct:fee", "acct:pool", "acct:author"):
        assert legacy not in account_ids, f"{legacy} 不该被建出来"
        assert led.balance(legacy) == 0, f"{legacy} 不该有任何余额"


def test_execution_guard_blocks_any_non_node_cut():
    """**运行时守卫**：换一个 PolicyRef 不能悄悄把抽成带回来。

    没有这道闸，"取消抽成"就只是 policy.py 里的一句注释 —— 谁把 DEFAULT_SPLIT
    改回去、或从别处传一个旧规则进来，都能让网络重新开始抽钱。
    """
    node = _u("node:guard")
    with pytest.raises(RuntimeError, match="不抽任何费用"):
        with tx():
            settlement.settle(_u("t"), _u("acct:guard-buyer"), node,
                              100, rule=LEGACY, commit=False)
    assert Ledger().balance(node) == 0, "守卫触发后整笔必须回滚，不留半截账"
