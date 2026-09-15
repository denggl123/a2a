# A2N 一期交易实现：账户 · 对等账户 · 达成交易（不碰钱）

> 日期：2026-09-08
> 口径对齐：**A2N 一期不做抽成生意，先把"能达成交易的网络"建立起来。**
> 第一阶段：多账户 + 按对等账户筛选 agent + 达成交易 + 双向对账。
> 积分计划是二期，由持牌托管商发行。本阶段全程不碰资金。

---

## 1. 一期与二期的分界

| | 一期（已实现） | 二期（预留） |
|---|---|---|
| 钱 | **完全不碰**：没有余额、没有冻结、没有划转 | 托管商发行积分，1 积分 = 0.01 元 |
| 账 | **事实账**：双方各自上报计量，A2N 只记录、只对账 | 资金账：账单 → 分账指令 → 托管执行 |
| 单位 | **分（fen）** | 积分（与分 1:1 兑换，接口已按此预留） |
| 信任来源 | 配对（熟人交易）+ 双向计量 + 对账 + 凭证链 | + 托管冻结 + 共识锚定 |
| 结清方式 | 双方自行结清（对公/线下），A2N 出对账凭证 | 托管商按结算指令实际划转 |

**关键设计**：一期输出的账单，就是二期分账指令的输入。二期接入时本层接口零改动。

## 2. 新增两个包（包数 19 → 21）

### a2n-account（L2）：账户与对等账户

- **多账户**：一个主体可维护多个结算账户（对公/对私/海外/不同业务线），
  `party_accounts` 表。A2N 不校验外部账户真伪，只记录"主体声明用它来结算"。
  注意与二期的 `accounts`（积分记账科目）严格区分，路由是 `/v1/party-accounts`。
- **对等账户（peer link）**：账户 ↔ agent 的配对。条款（单价、账期 net_days、
  额度 credit_limit_fen）双方确认后进入 ACTIVE —— **没有配对就没有交易**，
  这是一期把"陌生人交易"变成熟人交易的机制。
- **状态机**：PROPOSED → ACTIVE ⇄ SUSPENDED → CLOSED（非法迁移直接拒绝）。
- **条款只认已知字段**：塞进"evil_clause"这类 A2N 执行不了的条款会被丢弃。
- **额度检查用依赖注入**：`peers.set_exposure(deals.exposure_fen)`，
  L2 不反向依赖 L3（架构测试把关）。
- **settle**：agent 在 card 里声明 `accepts: ["peer_account"|"prepaid_points"]`，
  没声明 = 保守视为只接受预付费，一期筛不到。

### a2n-deal（L3）：一期交易

- **达成**：`deals.open(link, skill)` —— 配对必须 ACTIVE 且未超额度，
  **条款当场快照冻结**，事后改条款不影响已成交的单。
- **双向计量**：`deals.report(deal, party, dims)`，requester 与 provider 各报各的，
  两方都报才进入 DELIVERED。没给金额就按成交条款单价算，
  **无可计费计量 = 0 分，绝不兜底造一笔来源不明的钱**。
- **对账**：`deals.reconcile(deal)` —— 容差 = max(相对 10%，绝对 1 元)；
  一致 → RECONCILED（认定金额取双方均值）；分歧 → DISPUTED，
  **认定金额取较小者（宁可少收，不可多记），差额留人工仲裁**。
- **出账**：`statements.issue(link, period)` —— 聚合该账期已对账的交易，
  幂等（同账期重复出账返回同一张单）；出账后金额移出未结敞口。
- **状态机**：AGREED → DELIVERED → RECONCILED → CLOSED，任一步可 DISPUTED / CANCELED。

## 3. 多维度查找（dispatch 增强）

三级漏斗不变（能力匹配 → 属性过滤 → 信誉排序），筛选维度补全为 **13 个**，
全部以 Agent Card 声明的字段为准，可任意组合：

| 维度 | 筛选参数 | 说明 |
|---|---|---|
| 能力 | `skill` | 硬条件 |
| 结算方式 | `accepts` | **一期核心**：`["peer_account"]` 只看支持对等账户的 |
| 地域 | `region` | |
| 算力 | `gpu` / `vram_gb` / `concurrency` | 型号相等 / 显存与并发下限 |
| SLA | `max_latency_ms` | 延迟上限 |
| 技能标签 | `tags` | 交集匹配 |
| 计量维度 | `metering_dim` | 节点必须声明支持该计量（如 output_tokens） |
| 信誉 | `min_reputation` | |
| 可达性 | `reachable` / `online` / `connection_mode` | |
| 合规 | `kya_grade` / `status` | |
| 预算 | `max_price_hint` | **使用方消费行情**，不是平台定价；price_hint 仍不参与排序（铁律一） |

管理台发现页已加：GPU 型号 / 延迟上限 / 标签输入框 + "只看支持对等账户的"开关；
搜索结果直接标注"支持对等账户 / 仅预付费"，支持对等账户的可一键"用我的账户配对"。

## 4. 一期全链路（HTTP 冒烟已通过）

```
① 注册 agent（card 声明 accepts: ["peer_account"]）   POST /v1/registry/agents
② 使用方建多个账户                                    POST /v1/party-accounts
③ 与 agent 谈条款、配对、生效                          POST /v1/peers → accept
④ 筛选支持对等账户的 agent                             GET /v1/discover/peer-ready
⑤ 达成交易（条款快照冻结）                             POST /v1/deals
⑥ 双方各自上报计量                                     POST /v1/deals/{id}/report ×2
⑦ 对账（一致 297 分 / 分歧转争议取小）                  POST /v1/deals/{id}/reconcile
⑧ 出账（幂等）                                         POST /v1/statements
```

冒烟实测：provider 报 300 分、requester 报 294 分 → 容差内一致，认定 297 分
（双方均值）→ 出账 1 笔 2.97 元 ISSUED → 重复出账返回同一张单。

## 5. 二期衔接点（已预留，不改接口）

1. 托管商发行积分 → `accounts`（记账科目）与 `party_accounts`（结算账户）建立映射；
2. `statements` 的 `state: ISSUED → SETTLED` 由托管商回执驱动
   （`mark_settled(ref)` 入口已留，一期不触发）；
3. 结算指令从账单生成，走既有 `a2n-settlement`（分账 90/2/3/5）；
4. 预冻结模式（`prepaid_points`）接入时复用既有 `a2n-task` 的冻结/按实结算路径。

## 6. 测试

- 新增 `tests/test_deal.py` 12 项：多账户、条款清洗、状态机、无配对不交易、
  条款快照、对等账户筛选、双向上报、对账一致/分歧、额度拦截、出账幂等、
  不凭空造钱、多维筛选（GPU/并发/延迟/标签/计量/信誉/预算/组合）。
- 架构测试当场抓到 a2n-server 引用未声明依赖 → 已在 pyproject 显式声明。
- **全量 146 passed**。

---

## 7. 追加：调用门禁与连接投影（回答"card 里的连接会被替换吗"）

**问题**：agent 上架后，card 里的连接地址如果原样暴露，任何人都能绕过 A2N
直接调用节点 —— 计量、对账、门禁全部被跳过，越权。

> 更正（2026-09-14）：上述"直连节点跳过计量/对账/门禁"的合法路径已被收口。
> 程序化入口已统一为 `POST /v1/invoke`（402=支付挑战、403=带 hint），SDK `call_agent`
> 走治理链；`/v1/relay/{id}` 仅作对等账户的底层中继原语，使用端界面不出现它，控制台只调
> `/v1/invoke`。真实地址只给 owner，direct 节点自验 `X-A2N-Call`，不再有绕过治理链的入口。

**回答分两层**：

1. **注册表里的原始 card 不改**（card_hash 要校验能力没被偷偷改弱）；
   但**发现结果对外投影时统一改写**：使用方拿到的永远是 A2N 的门牌号
   `/v1/relay/{agent_id}`，direct 节点的真实 url 和平台观测的 peer_ip 都不
   出注册表（只留给平台内部协商使用）。pull（NAT 后）节点连门牌号都没有——
   它只出站拉单，本来就不该被直连。

2. **中继入口不是公共跳板**：调用必须出示凭据（`X-A2N-Call` header）。
   - 签发：`POST /v1/transport/call-token`，只给**与该 agent 存在 ACTIVE
     对等账户配对**的使用方（一期权限边界；二期加"预算已冻结"）；
   - 校验：HMAC-SHA256 签名 + 10 分钟过期 + agent 绑定，三关全过才转发；
   - 透传：凭据验出的调用方身份随转发请求下发（`caller`），节点侧知道
     "这次是谁在调"；请求能从隧道进来必然已过平台这道门，节点零校验成本。
   - SDK：`client.call_agent(agent_id, path, body)` 自动取凭据再调用——
     **用 SDK 的双方才能互相识别**，这正是这道门存在的意义。

**冒烟实测**：无凭据 401 / 无配对签发 403 / 真凭据过门禁到达转发（409 无隧道）
/ 篡改凭据 401 签名无效 / 跨 agent 绑定校验拒绝。

**测试**：新增 `tests/test_call_gate.py`（凭据签发/篡改/过期/绑定、连接投影不泄露
真实 url 与 peer_ip、pull 节点不给 url）。另修复：测试环境禁用真实入站探测
（`A2N_PROBE_DISABLED`，conftest 统一设置），否则多几个 direct 节点会把全量
拖慢近半分钟。**全量 149 passed，9.8s。**
