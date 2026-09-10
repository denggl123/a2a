# A2N 调用参数规范：使用方视角的数据归属

一句话：**agent 调用，使用方只关心两类事——"选谁"（发现/选型参数）和"这一单怎么样"（调用/事后参数）。
每个参数必须有唯一的主人：服务端声明的、平台探测记账的、使用端实测的，谁也不能替谁说话。**

## 铁律

1. **声明归服务端**：能力、价目、结算方式、算力、SLA 承诺——供给方在 card 里写，
   平台只存快照不改写（改了就是第二个真相）。
2. **记账归平台**：信誉、KYA、状态、账目——平台从事件折算，节点和使用方都不能直接写。
3. **实测各归各端**：服务端的执行体验（TTFT）只有服务端 SDK 测得到；端到端往返只有使用端
   测得到（P2P 下平台测不了）。谁测的谁自报，平台只聚合展示。
4. **展示必须标注口径**：`声明 / 自报 / 探测 / 使用端`，无标注的延时数字不许出现在发现页。
5. **实测要防刷**：绑定 task_id 时平台严格校验任务两端（requester/node）；无绑定回传只进
   窗口（一期），生产应强制绑定。

## 发现/选型参数（使用方在发现页看的）

| # | 参数 | 归属/口径 | 数据在哪 | 采集方式 | UI 标注 |
|---|------|----------|---------|---------|---------|
| 1 | skills 能力 | 服务端声明 | card（agents.card_json 快照） | 上架时写入 | （声明） |
| 2 | price_book 价目 | 服务端声明 | card；算法解释在 settlement.price | 上架时写入 | （声明） |
| 3 | accepts 结算方式 | 服务端声明 | card（peer_account / x402 / …） | 上架时写入 | （声明） |
| 4 | compute 算力 | 服务端声明 | card → agents.compute | 上架时写入 | （声明） |
| 5 | sla.max_latency_ms | 服务端承诺 | card → agents.sla | 上架时写入 | SLA 声明 |
| 6 | ttft_avg_ms 首响 | **服务端实测自报** | agents.connection.metrics | 服务端 SDK 执行时滑窗（20 次）→ 心跳自报 | 自报 |
| 7 | rtt_ms 连接 | 平台探测 | agents.connection.rtt_ms | probe 入站探测（仅 direct 模式） | 探测 |
| 8 | observed.total_avg_ms 端到端 | **使用端实测** | agents.connection.observed | 使用端 SDK 调用计时 → observations 回传（滑窗 20） | 使用端 |
| 9 | reputation / kya / status | 平台记账 | agents 列 | 事件折算（bump_reputation 等） | 平台 |

> 延时三口径的语义：`SLA 声明`是承诺上限（筛选用）；`自报`是服务端最近体验；
> `探测`是平台↔节点；`使用端`是唯一真正的"两个 SDK 之间"往返。数值互不可比、不可换算。

## 调用参数（使用端发起调用时给的）

| 参数 | 归属 | 说明 |
|------|------|------|
| agent_id / path / body | 使用端 | 调谁、调什么 |
| budget | 使用端 | 预算上限（合约价封顶），冻结与否看结算模式 |
| currency | 使用端 | 必须与配对条款/价目币种一致（成交不许换币） |
| token（call 凭据） | 平台签发 | 短命凭据，服务端校验配对关系 |
| timeout | 使用端 | 一期隐含（中继 15s），规范上属使用端 |

## 事后参数（调用完成后各方拿到什么）

| 参数 | 归属 | 流向 |
|------|------|------|
| usage 计量 | 使用端提交（节点代报） | tasks 表，验收复算 |
| amount / amount_minor | 平台记账 | 按 amount_of（合约价）算出，四方表贯穿 |
| result_hash | 平台刻章 | notary 凭证链 |
| ok / total_ms 观测 | **使用端实测** | 自动回传 observations（best-effort） |
| ttft_ms 单次 | **服务端实测** | 进服务端滑窗，随下次心跳自报 |

## 数据落点速查（谁写哪张表/哪个字段）

```
服务端 SDK（节点）──心跳──▶ agents.connection.metrics   （TTFT 自报）
                            agents.connection.mode/nat  （连接声明）
服务端 SDK（节点）──上架──▶ agents.card_json/compute/sla （声明，卡哈希背书）
平台探测/记账     ──────▶ agents.connection.rtt_ms       （探测，心跳不覆盖）
                            agents.reputation/kya/status（事件折算）
使用端 SDK       ──回传──▶ agents.connection.observed    （端到端实测，绑 task 防刷）
使用端 SDK       ──调用──▶ tasks/ledger/deals…           （经门禁与验收，不直写）
```

## 接口

- `POST /v1/registry/agents/{id}/observations`（X-Principal = 使用方）
  body: `{task_id?, rtt_ms?, total_ms?, ok?}`；绑 task 时校验 requester/node 两端。
- 心跳 `POST /v1/registry/agents/{id}/heartbeat` 增加 `metrics` 字段（服务端自报）。
- SDK：`call_agent()` 自动计时回传；显式 `observe()` 可带 task_id。

## 地址与绕行：拿到 card 也不能绕开 A2N

**结论：能绕的是"直连节点"，绕不开的是"门禁、计量、结算、刻章"。**

| 场景 | 能否直连节点 | 后果 |
|------|------------|------|
| pull / wss / relay / tunnel | **物理上不能** —— 节点没有公网入口，卡里的地址是本机/内网 | 只能走平台中继 |
| direct（节点自报公网地址） | 地址存在就可能被直连 | 平台替它守不住，**必须节点自己验凭据** |

平台侧的防线（已落地）：

1. **对外一律只发 A2N 门牌号** `/v1/relay/{agent_id}` ——
   `GET /v1/registry/agents`、`GET /v1/registry/agents/{id}`、discovery
   三条路径口径一致；`card.url` / `connection.url` / `card_url` 三处都投影，
   真实地址只给 owner 自己（上架回填要用）。**投影后直连打到的仍是平台入口**，
   绕不过门禁、计量、对账与刻章。
2. **card_hash 不动**：它背书的是原始卡，不是投影卡。
3. **节点自守门（零信任）**：direct 节点公网可被直连，平台只能"答真伪、
   不做决定" —— 节点收到 `X-A2N-Call` 后调 `POST /v1/transport/verify-token`
   问平台，验不过就 401。SDK 侧 `serve_local_agent(..., verify=platform_verifier(...))`
   一行接上；`bind` 开到公网就必须同时给 `verify`，否则等于白送能力。

绕行调用意味着什么（对使用方是坏交易）：
- **没有凭证**：平台没刻章，这笔调用在凭证链上不存在；
- **没有验收**：结果对不对没人判，争议时 A2N 无据可依；
- **没有账**：供给方收不到钱，回头仍可向使用方主张（双边记账/直付各自有据）。
