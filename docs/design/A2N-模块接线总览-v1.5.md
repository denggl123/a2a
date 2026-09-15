# A2N 模块接线总览 v1.5

> 本文回答一个问题：**20 余个包之间，谁连谁、靠什么连、为什么这么连。**（原「19 个包」已过时：后续新增 a2n-settlement 结算收口、a2n-node 自持模式等包）
> 代码落点：`a2n/packages/a2n-server/src/a2n_server/wiring.py` —— 所有接线只写这一个文件。
>
> 更正（2026-09-14）：① 自证闸收口——`a2n-registry` 现依赖 `a2n-p2p`（`verify_selfproof`/`card_body` 为单一源，`verify_card`/`card_verdict` 三态 `signed`/`unattested`/`invalid`+`tampered` 为读侧唯一出口；`_guard_selfproof` 拒收自称身份却验不过的卡）。② 结算收口——新增 `a2n-settlement` 包（`closing.py`；`settlements`/`reconciliations` 两表，`record()` 合并两条记账路）。③ 新增第 23 包 `a2n-node`（自持模式，见 `docs/SOVEREIGN.md`：无发行方/无中心账本/无全局目录，模块 home/card/receipt/peer/node，门禁下沉到每节点）。④ relay 收口——`/v1/relay` 仅作对等账户底层中继原语，使用端统一走 `POST /v1/invoke`。

## 一、总图

```
                         ┌─────────────────────────────────────────────┐
                         │                a2n-server (L5)              │
                         │   app.py 装配 HTTP · wiring.py 接线 · console │
                         └───────┬─────────────────────────┬───────────┘
                                 │ 依赖注入                 │ 事件订阅
   ┌─────────────────────────────┼─────────────────────────┼──────────────────┐
   │  服务层（顶层）                                                          │
   │  a2n-task ──acceptance──settlement──wallet                              │
   │      │            │            │                                        │
   │  A2A v1.0 视图   争议 dispute  退款执行 refund                            │
   └──────┼────────────┼────────────┼────────────────────────────────────────┘
          │ 事件        │ 事件        │
   ┌──────┼────────────┼────────────┼────────────────────────────────────────┐
   │  管理层（第二层）                                                        │
   │  registry · transport · reputation · dispatch · notary · market          │
   └──────┼────────────┼────────────┼────────────────────────────────────────┘
          │            │            │
   ┌──────┼────────────┼────────────┼────────────────────────────────────────┐
   │  网络层（底层）                                                          │
   │  a2n-p2p（DID/发现/gossip/签名器）· a2n-consensus（epoch/SPV/三钥匙）      │
   │  a2n-kernel（哈希链/Merkle/事件/策略）· a2n-store（append-only）          │
   └───────────────────────────────────────────────────────────────────────┘
```

## 二、接线清单（wiring.py 逐条）

| # | 线 | 机制 | 为什么这么连 |
|---|---|---|---|
| 1 | kernel.events → store.outbox | `events.set_sink()` | 内核不许碰存储；落库由装配层注入 |
| 2 | ledger → consensus.batch_provider | 依赖注入回调 | 共识需要知道账本，但不 import 它（无环） |
| 3 | custodian.balance_fen → consensus.set_escrow | 依赖注入回调 | 资金锚读数只能来自唯一能碰钱的包 |
| 4 | p2p.signer → consensus verifier/witness/anchor | 依赖注入 | 密码学算法是能力不是角色，共识不绑死任何库 |
| 5 | acceptance.failed → disputes.open | 事件订阅 | 机器判不了的自动转人工，材料一次性固化 |
| 6 | arbitration.resolved → settlement.refund | 事件订阅 | 裁定与执行分离：仲裁判断，结算划转 |
| 7 | epoch.anchored → p2p.gossip(ANCHOR) | `attach_p2p()` + 事件订阅 | 定案向网络广播；p2p 可后挂，节点重启重 attach |
| 8 | p2p.query offers → rosters | `p2p_discover_to_roster()` | 发现是一次网络行为，结果沉淀为本地市场列表 |

## 三、共识：三把钥匙

```
open() ──→ seal() ──────→ finalize()
记起点      算 Merkle 根    集齐三把钥匙
            (节点)          ├─ 钥匙一 账本：重算哈希链 + Merkle 根（抓篡改）
                           ├─ 钥匙二 网络：≥2/3 见证权重（TOFU 名单只增不改）
                           └─ 钥匙三 持牌方：资金锚签名（托管余额声明）
```

- 停摆优于错误通过：缺任何一把，epoch 停在 SEALED，绝不降级放行。
- SPV：`POST /v1/consensus/proof` 用 O(log n) 证明单条账目在已锚定批次里。
- 演示妥协：见证人与锚的私钥在本进程内存生成；生产替换点 =
  见证人签名经 p2p EPOCH/ANCHOR 消息到达、资金锚由持牌方 HSM 签发。

## 四、AP2 ⇄ A2N 翻译

```
用户(人)                 agent(A2N)                    节点(被雇的 agent)
  │ Intent 授权(分)          │                              │
  ├──────→ BudgetTranslator │                              │
  │         → A2N 冻结预算   ├─ tasks.create(冻结) ─────────→ 执行
  │                          │                              │
  │                          ←─ 验收 + 分账（结算层）         │
  │ Payment Mandate ←── ReceiptBuilder（四件套证据回填）      │
  └── 出事 → AccountabilityPack → 争议 → 仲裁 → 结算层退款    │
```

1 积分 = 0.01 元 = 1 分：翻译 1:1，无汇率、无换算、无套利空间。

## 五、A2A v1.0 状态映射

```
A2N:  CREATED → ASSIGNED → SUBMITTED → ACCEPTED → SETTLED
         │         │            │
         │         │            └── REJECTED（验收不通过，A2A 视角 failed）
         │         └── FAILED（没干完，A2A 视角 failed）／CANCELED
         └── CANCELED
A2A:  submitted → working  → completed（SUBMITTED/ACCEPTED/SETTLED 均）
```

非法迁移在 `_transition()` 一处拒绝；A2A 视图 `GET /v1/tasks/{id}/a2a`。

## 六、测试地图（132 passed）

| 文件 | 覆盖 |
|---|---|
| test_invariants | 三铁律（增发白名单 / 总量≡托管 / append-only） |
| test_architecture | 依赖声明、无环、SDK 零依赖、只有 custodian 碰钱 |
| test_consensus | Merkle、epoch 三段、2/3 门槛、锚、SPV、篡改检测 |
| test_ap2 | 授权链双向金额锁、签名、预算冻结、凭证、问责包 |
| test_a2a_state | 迁移表、A2A 映射、cancel/fail、零计量打回 |
| test_arbitration | 开单、裁定、自动开单、改判自动退款、总量守恒 |
| test_wiring | wire 幂等、阈值封口、ANCHOR 广播、roster 落地、份额追回 |
| test_flow / test_connection / test_transport / test_p2p | 原有闭环与网络层 |
