# A2N 双方反馈接口契约（R2）

状态：**v0.1 设计稿；F0/F0b/F2/F1/F3 已落地**。本文把 [双方反馈规则](FEEDBACK-RULES.md) 落成开发接口；消息版本名 **a2n-feedback/1**。方向与身份口径已由产品方确认（**买卖双方都用节点 did，一个 did = 一个节点**），字段、路径和默认数值仍须通过实现与试点验证。已落地：本机账本（`a2n_sdk.feedback.FeedbackBook`）、`POST /v1/feedback/open|revise`、`POST /v1/feedback/deliver`（第 4 节捎带的「补交」入口）、节点身份签名/验签（`a2n_node.feedback_identity`）、控制台「双方反馈」面板。**尚缺**：`GET /v1/feedback`、`…/{id}/versions`、`/v1/feedback/summary` 三个只读接口（控制台当前用管理面快照列表代替）。

编写日期：2026-09-30。下表路径是设计，不表示已落入代码。

## 1. 不变量

- 反馈键 = **(task_id, direction, author_did)**；每方向一份当前有效反馈，修改 = 新 `revision`，不删旧版。
- 作者身份 = 节点身份 `did:a2n:ag_*`（ed25519 公钥指纹）；`proof = {pub, sig}`，无填充 URL-safe Base64，与协调层 `Proof` 同口径。
- `quality` 维度**只允许**任务状态为 `COMPLETED`/`ACCEPTED`/`SETTLED` 的任务（`ACCEPTED`＝调用方本地投影的「已验收」成功态）；服务端硬闸，客户端提示不作为闸门。
- 反馈与结算、与争议两条线互不写账：反馈操作不产生任何账本变动。
- 收到的对方反馈**验签后原样留存**，不合并进自己的反馈表，不代签、不转写。

## 2. 公共数据形状

| 类型 | 必需字段及类型 | 语义 |
|---|---|---|
| SignedFeedback | v: "a2n-feedback/1"；feedback_id: string；task_id: string；direction: string；author_did: string；counterparty_did: string；provider_did: string；service_id: string；task_state: string；dimensions: object；note: string（可空）；source: string；at: integer；revision: integer；prev_hash: string（首版为 ""）；proof: Proof | 一份完整反馈；验签覆盖除 `proof` 外的完整原对象 |
| dimensions | `{quality?, punctual?, communication?, on_spec?, cooperative?}`，每维 1–5 整数，可留空 | 留空不算差评也不算好评；`quality` 受状态硬闸 |
| direction | `buyer_to_seller` 或 `seller_to_buyer` | 作者与 counterparty 由服务端按任务实际角色校验，不信客户端自报 |
| source | `self` / `counterparty` / `third_party_unverified` | 本机自写 / 对方签名捎带 / 无节点签名的第三方记录 |
| Proof | {pub: string, sig: string} | `did:a2n:ag_*` 必须等于 `pub` 的指纹；不符即拒绝 |
| FeedbackView | SignedFeedback + verified: boolean + self_source: boolean | 展示层视图；`verified=false` 的原样留痕但不进有效统计 |

时间字段为 Unix 秒。实现拒绝空标识、非 1–5 的维度值、未知 direction、过大 `note`（建议 ≤ 500 字符）和未知的强制字段；可忽略明确定义为可选的未知字段。

## 3. 本机受保护 HTTP 接口

所有 `/v1/feedback/` 接口复用现有管理面本机来源、Host/Origin 与配对令牌校验（与 `/v1/disputes`、`/v1/projections` 同层）。

| 方法与路径 | 请求要点 | 响应 |
|---|---|---|
| POST /v1/feedback/open | task_id、direction、dimensions、note；`Idempotency-Key: command_id` | 201 FeedbackView；同键重放返回原结果；同方向已有有效反馈 → 409 并指明走 revise；任务不存在/不属本节点 → 404；未交付却评 `quality` → 400 |
| POST /v1/feedback/revise | feedback_id、dimensions、note；`Idempotency-Key` | 200 新版本 FeedbackView（revision+1，prev_hash 链接）；旧版可查不可改 |
| POST /v1/feedback/deliver | task_id、scope | **F3 补交**：把本机买方反馈随**同一份收据回执**再捎一次给供给方，并收下对方随响应捎回的反馈。返回 `{delivered: bool, received: bool, reason?}`；没有回执 / 没写反馈 / 对方不在线都 `delivered=false` 并给 `reason`（不假装送达）|
| GET /v1/feedback | 可选 task_id、direction、counterparty、cursor、limit | 分页列表（FeedbackView[]）；含验签状态与来源标记 |
| GET /v1/feedback/{feedback_id}/versions | — | 版本链（旧版只读） |
| GET /v1/feedback/summary | counterparty 或 provider_did | **只给事实计数**：条数、各维平均（注明样本数）、最近更新时间、self_source 与未验签计数。**不给信誉分、不给等级** —— 那是 R3 |

## 4. 节点间最小交换（R2 只做捎带）

R2 不做按需拉取/摘要服务（R3）。最小路径：**反馈签名对象随任务终结消息捎带**，接收端验签后按 `source=counterparty` 原样留存：

```
任务终结消息.metadata.feedback = SignedFeedback
```

- 接收端流程：`verify(proof)` → did==指纹 → 校验 task_id/direction/角色与本机账本一致 → 通过则留存并标记 `verified=true`；任一步失败 → 留痕 + `verified=false`，**不采信**。
- 对端不在线/未捎带：不影响本机自写反馈的有效性；补交走同一捎带路径（`POST /v1/feedback/deliver` 重发同一份收据回执当载体，供给方 `acknowledge` 幂等），不做专门重传协议。
- 方向在实现里的落点：`buyer_to_seller` 随**调用方回执**（`/a2n/ack`）上行；`seller_to_buyer` 随**供给方回执响应**下行。反馈通常写在调用之后，故由「补交」重发回执触发；供给方一侧的反馈**不主动推送**，等调用方下次补交/重捎回执时送达。
- 反馈对象与任务一样**同 task_id 绝不重发**已处理过的版本；重放按幂等处理，不重复入库。

## 5. 进程内端口（逻辑签名）

| 端口 | 方法 | 约束 |
|---|---|---|
| FeedbackBook（a2n_sdk，LocalStore 之上） | open / revise / list / versions / ingest(signed) | 全部走 `store.tx()` 原子写；open 校验任务归属与状态硬闸；ingest 只验签+留存，永不写自己的评分 |
| 节点身份适配（a2n_node） | sign(feedback) / verify(signed) | 复用 `a2n_p2p.identity.Identity` 与 `verify_pub`；不新增密码学原语 |

## 6. 开发批次与验收

批次与验收逐条见 [FEEDBACK-RULES.md](FEEDBACK-RULES.md) 第 8、9 节（F0 本地账本 → F1 控制台 → F2 验签留存 → F3 捎带交换 + R2 全量验收）。每批完成后以实际行为验收，再继续下一批；R2 全绿后才进 R3（算分与排序）。
