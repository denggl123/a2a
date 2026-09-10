# A2N 聚合 Agent：把多个成员聚合成一个稳定的虚拟服务

一句话：**你自己建一个虚拟的财务 agent，它后面挂着 N 个真实财务 agent；
使用方只看到这一个稳定的门面，谁来干活、挂了怎么办，是聚合器的私事。**

## 模型：虚拟卡 + 聚合执行器

```
使用方 ──调用/派单──▶ 虚拟 agent（普通卡：自己的名字/价目/技能）
                          │  AggregateNode（a2n-sdk，跑在创建者机器上）
                          │  策略选成员 → call_agent 转发 → 失败换人重试
                          ▼
        成员 agent A(w5)   成员 agent B(w1)   成员 agent C(w2) ...
```

三条架构判断（为什么这样落）：

1. **平台零感知**。虚拟 agent 就是一张普通卡——注册、发现、门禁、计量、结算
   全走既有链路。聚合是供给方的自由（我怎么提供能力是我的事），不是平台的新事实；
   表归属、三铁律一行不用动。平台不需要 groups 表，不需要新端点。
2. **聚合器是成员的"使用端"**。转发走 `call_agent`，自动计时、自动回传观测
   —— 成员健康数据天然落进上一轮的使用端实测口径（谁测的谁自报）。
3. **策略注册制**。`register_strategy(name, fn)`，与 `settle.register_settler`
   同一模式：新策略 = 注册一个函数，分派主体零改动。

## 策略

| 策略 | 行为 |
|------|------|
| `round_robin` | 轮询：游标依次起头，失败顺延 |
| `weighted` | 平滑加权轮询（SWRR）：高权重先被选、占比≈权重比，低权重也保证轮到 |
| （自定义） | `register_strategy("name", fn(members, state) -> [尝试顺序])` |

**failover 是所有策略共用的兜底**，不是独立策略：

- 单成员失败 → 换下一个重试（默认尝试次数 = 成员数，可配 `max_tries`）；
- 失败成员进**冷却**：时长 = `cooldown_ms × min(连续失败次数, 5)`——连挂连加长，
  到期自动恢复（"失败定时刷新状态"）；下次选择自动跳过冷却中的成员；
- **全员冷却时半开探测**：宁可试一个冷却最早到期的，也不直接把任务判死；
- 全员真失败 → `cancel_task(原因="聚合成员全部失败")`，任务不悬空。

> 权重是聚合器的配置（声明），高权重挂了进冷却自然落到低权重——
> 稳定性由 failover 兜底，不由权重许诺。这就是"高权重可以用就先用高权重的"。

## 账目闭环（谁跟谁结，两本账互不混）

```
使用方 ──平台结算──▶ 虚拟 agent     （虚拟 agent 是节点身份，走 tasks/ledger/notary）
虚拟 agent ──对等账户/直付──▶ 成员   （聚合器是成员的使用端，账走各自账）
```

- 使用方只跟虚拟 agent 结算：预算、验收、刻章全在平台既有链路上；
- 虚拟 agent → 成员的成本走对等账户（成员侧 `auto_accept` 可全自动）或直付；
- **差额就是聚合器的毛利**——平台只刻章不碰钱，两边账各自成立。

## 成员前提

成员必须能被聚合器调用到（`docs/CALL-PARAMS.md` 数据归属不变）：

1. 成员可被中继调用（relay/tunnel 模式）——聚合器经平台中继入口打它，
   接触不到成员真实地址；
2. 与聚合器 ACTIVE 配对（`call_token` 的前提）——成员侧 `auto_accept=True`
   可自动完成配对。

## 用法

```python
from a2n_sdk.aggregate import AggregateNode

finance_card = {  # 虚拟卡：像上架普通 agent 一样填
    "name": "my-finance", "version": "1.0.0",
    "url": "http://localhost:9200/a2a",
    "skills": [{"id": "fin-strategy", "tags": ["finance"]}],
    "x-a2n": {"price_book": {"fin-strategy": {"CNY": {"dimensions": [
        {"key": "call_count", "amount": 12, "per": 1}]}}}},
}

node = AggregateNode(
    card=finance_card,
    members=[{"agent_id": "ag_big",  "weight": 5},   # 高权重：主用
             {"agent_id": "ag_mid",  "weight": 2},
             {"agent_id": "ag_back", "weight": 1}],  # 兜底
    principal="acct:me", strategy="weighted", cooldown_ms=30_000)
node.serve(tunnel=True)   # 常驻接单；心跳 metrics 自带成员健康
```

可观测：

- 平台侧：虚拟 agent 的卡、价目、TTFT 自报、使用端实测——和普通 agent 同一发现页；
- 心跳 `metrics.aggregate`：每个成员的 ok/fail/冷却中/RTT 均值（自报口径）；
- 本地管理台 `snapshot()["local"]["members"]`：成员健康明细。
