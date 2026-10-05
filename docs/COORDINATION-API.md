# A2N 协调层接口契约

状态：**v0.1；C0 契约与 C1–C3 运行实现已落地，C4 已接入本地数量策略和控制台（2026-10-05）**。线上消息版本为 **a2n-coord/1**。以下保留设计契约；当前实现范围、试点参数和差异以 [SDK-ARCHITECTURE.md](SDK-ARCHITECTURE.md) 为准。跨公网试点仍待验证。

实现：`a2n_sdk.coordination` / `coordination_service` / `coordination_api`，节点侧 `coord_identity` / `coord_service` / `coord_network` / `coord_mailbox`。搜索接口沿用本机授权；六个公共操作使用签名封套。当前转送限定一个明确邻居，暂不接受嵌套转送。安全探测只核验签名元数据，业务可达性保持未知。

**错误口径已对齐 §7（2026-10-05）**：验签通过后的一切失败——限流、容量不足、超时、未找到、请求/响应过大——统一回**已签名 ERROR 封套**，HTTP 状态取 §7 表，限流附 `Retry-After`；`body` 用契约的 `message`（发送端同时兼容读取 `error`）。验签不通过时只回最小 JSON（`401 UNVERIFIED_IDENTITY`），不为其签名、不泄露目录。调用方据此区分"被限流"与"身份不对"，不再只看到一个裸状态码。

继续接口可附带 `preferences` 更新本机满意目标；在途读操作尚未完成时返回 BUSY。基础公共发现不能关闭，可选服务经 `/v1/public-services` 分别管理。旧 `/v1/discovery/search` 保持结果与来源标签兼容，并附带搜索会话快照。

## 1. 不变量与身份

- 商品键为 **(provider_did, service_id)**。同商品可有多张不同版本或不同通道的 Card；Card 哈希不是永久商品 ID。
- 协调只产生候选、来源、进度和通道方案。**SATISFIED 不表示购买**；搜索、评估、探测和 route-plan 均不得调用 Agent、占用试用、验收或扣款。
- 引荐路径与执行路径分别记录。B 引荐 C 不授权 B 转发 C 的业务任务，也不证明 C 在线。
- 每个可调用通道绑定供给方已验签的完整 Card、**route_card_hash** 和 **channel_type**；第三方的任意 URL 不能覆盖签名 Card。
- 控制口探测只产生 **control_reachable** 观测，不能推出业务任务可执行。无法安全探测任务协议时，**task_reachable** 保持 unknown。
- A 拥有搜索队列和全局额度；远端每次只处理有界请求，不替 A 自主递归。

### 1.1 公共数据形状

| 类型 | 必需字段及类型 | 语义 |
|---|---|---|
| CandidateKey | provider_did: string；service_id: string | 商品身份；两项都不可为空 |
| NodeRecord | node_did: string；versions: string[]；coord_routes: CoordRoute[]；operations: string[]；issued_at/expires_at: integer；proof: Proof | 节点自签的协调入口声明，不能当在线保证 |
| CoordRoute | channel_type: direct_https、local_http 或 coord_mailbox；endpoint: string；可选 relay_did: string | 只用于协调消息；mailbox 必须有明确承载者及租约；local_http 仅适用于本机明确授权的回环／局域网范围，远端引荐不能授予该权限 |
| Referral | introducer_did: string；target_record: NodeRecord；target_record_hash: string；skill_hint: string[]；observation: string；observed_at/expires_at: integer；proof: Proof | 引荐者签名的观察，保留目标自签声明 |
| OfferHint | provider_did/service_id: string；skills: string[]；card_ref: object；card_hash: string；observed_at: integer | 来源节点给出的线索，未取卡验签前不可调用 |
| Candidate | key: CandidateKey；cards: VerifiedCard[]；sources: object[]；routes: RouteDescriptor[]；verification: string | 同商品多来源合并，Card 版本和通道分开保存 |
| RouteDescriptor | route_id: string；key: CandidateKey；channel_type: direct_a2a 或 sealed_relay_a2a；route_card_hash: string；card: object；expires_at: integer；可选 relay_did: string | Card 必须由 provider_did 签名，类型须与真实传输适配器相符 |
| RouteObservation | route_id: string；measured_by: string；at: integer；probe_kind: string；control_reachable: boolean 或 null；task_reachable: boolean 或 null；可选 rtt_ms: number | null 表示 unknown；本机实测、自报、第三方报告必须分开 |

Proof 为 {pub: string, sig: string}，均用现有无填充 URL-safe Base64 口径。时间字段为 Unix 秒；过期边界是 **now >= expires_at 即无效**。实现拒绝空标识、过大字段、非有限数字和未知的强制字段；可忽略明确定义为可选的未知字段，但验签仍覆盖完整原对象。

## 2. 进程内端口

接口描述为逻辑签名，语言绑定可采用 dataclass、Protocol 或等价类型。SDK 的协调端口保持零 a2n_node 依赖，由节点装配网络与身份适配器。

### 2.1 本地业务 → CoordinationPort

| 方法 | 输入 | 输出与约束 |
|---|---|---|
| start(spec, command_id) | SearchSpec | 创建 search_id、revision=1、首轮额度；command_id 幂等 |
| get(search_id) | string | SearchSnapshot；纯本地读 |
| pause(search_id, command_id, expected_revision) | 标识、整数 | 停止新派发，保留 frontier；幂等 |
| resume(search_id, command_id, expected_revision, round_budget) | 同一 search_id、新一轮额度 | round 加一，沿用条件及已验证候选；新逻辑网络请求用新 request_id |
| cancel(search_id, command_id, expected_revision) | 标识、整数 | 终止搜索；不取消已创建的业务任务 |
| evaluate(search_id, command_id, expected_revision) | 标识、整数 | 将当前候选快照交给本地策略，更新决策；不执行交易 |
| plan(search_id, key, command_id, expected_revision, probe) | CandidateKey、boolean | 返回通道优选及观测；probe=true 只做安全控制探测，并扣搜索额度 |
| candidates(search_id, result_cursor, limit) | 本地结果游标 | 一页 Candidate 摘要，详情可按 key 读取；与远端 FIND 游标无关 |

SearchSpec 至少包含 skill: string、required: object、preferences: object、policy_id: string、policy_version: string、round_budget: Budget、auto_continue: boolean；自动多轮时必须有 **total_budget: Budget**，且各轮累计消耗不得超过它。精确预算与私有偏好只在本机存储；远端 FIND 仅发送允许公开的粗粒度条件。Budget 至少限制远程操作数、接收字节、总时长、深度、候选数、主动探测数和并发数。每次发出网络操作前预留额度；即使崩溃后无法确认是否发出，也计为已用。

SearchSnapshot 至少返回 search_id、revision: integer、progress_revision: integer、result_revision: integer、round: integer、state、spec_version、budget_used、budget_remaining、candidate_count、frontier_count、stop_reason。revision 是控制版本，在控制命令、额度授权或状态转换时递增；progress_revision 记录后台进度与额度消耗，result_revision 记录候选及影响选择的观测变化。普通后台收包不会不断改变控制版本，避免用户无法暂停。枚举状态及默认额度见主规则第 7、11 节。

Budget 的字段固定为 remote_operations、received_bytes、duration_ms、introduction_depth、candidate_limit、probe_operations、max_concurrency，均为正整数且不得以 boolean 代替。remote_operations 包括每次实际发送、转送预留、拉卡和探测；probe_operations 是其中的子额度，不能被当成额外赠送的网络操作。total_budget 中操作数、字节数与运行时长按各轮累计；深度和并发数是上限，候选数按同商品去重后的集合计，不按轮相加。会话暂停时间不计运行时长，但仍受主规则会话有效期限制。

VerifiedCard 为 {card: object, card_hash: string, verified_at: integer, provider_did: string, service_id: string}。Candidate.verification 取 HINT、CARD_VERIFIED、CONFLICT、EXPIRED、INVALID；CARD_VERIFIED 只说明声明通过验证，可调用性仍由通道与业务条件分别判断。接口中的业务 required/preferences 由指定 policy_id/policy_version 在本地校验，协调协议不为未知业务字段猜测含义。

### 2.2 LocalCandidatePolicy

本地策略接口为 **evaluate(candidate_snapshot, policy_version, budget_remaining) → PolicyDecision**。输入是同一 result_revision 下的不可变候选快照及来源、未知字段；输出 decision ∈ {SATISFIED, CONTINUE, NEEDS_INPUT}、reason_codes: string[]、可选 selected_keys: CandidateKey[]。策略可以决定停搜、继续或请求人确认，不能直接创建业务任务。远端响应不得携带待执行的策略代码。策略版本变化时重新评估，不复用旧结论。

评估在新候选或重要观测到达、一轮结束、用户显式请求时触发，并应合并密集触发。PolicyDecision 返回所用的 result_revision 和 policy_version；协调层提交决策前核对版本，已变化则重新评估。CONTINUE 只使用当前授权额度；新一轮需要 auto_continue 的有效总额度或用户新的 resume 命令。NEEDS_INPUT 暂停自动探索并保留待确认原因。

### 2.3 CoordinationNetworkPort

| 操作 | 必要结果 |
|---|---|
| connect(node_record, deadline) | 已认证会话和实际 peer_did；两者不符则失败 |
| exchange(session, signed_request, response_cap) | 有界、可验签的协调响应；报告实际收发字节和错误 |
| safe_probe(coord_route, nonce, deadline) | 只证明协调控制口响应，记录观测时间与 RTT；不发送业务任务 |
| probe_task_route(route_descriptor, nonce, deadline) | 通过相应协议的安全元数据／控制操作核对目标与通道；没有安全操作则返回 unknown，不调用商品 |
| forward(session, forwarding_envelope, response_cap) | 沿 A 指定的有限路径向唯一目标转送；各跳只发给指定下一跳，返回目标原始签名响应及转送状态 |

network 端口不得将经过 UDP 打洞的控制连接自动视为 HTTP/A2A 任务通道。适配器必须限制解析、重定向、地址类别、响应体和超时；公网引荐不能使客户端请求回环或未授权内网管理口。

## 3. 本机受保护 HTTP 接口

所有 **/v1/coord/** 本机接口复用现有管理面本机来源、Host/Origin 和配对令牌校验；反向代理公开 Card 的许可不等于管理权限。响应包含 search_id、revision 和状态；不向公共入口暴露私有 spec/frontier。下表路径是设计，不表示已实现。

| 方法与路径 | 请求要点 | 响应 |
|---|---|---|
| POST /v1/coord/searches | SearchSpec；Idempotency-Key: command_id | 201 SearchSnapshot；同命令同内容重放返回原结果 |
| GET /v1/coord/searches/{search_id} | 无 | 200 SearchSnapshot |
| POST /v1/coord/searches/{search_id}/pause | If-Match: "revision"；Idempotency-Key | 200 SearchSnapshot |
| POST /v1/coord/searches/{search_id}/resume | 同上；body.round_budget | 200 同一 search_id、新 round 的快照 |
| POST /v1/coord/searches/{search_id}/cancel | 同上 | 200 SearchSnapshot |
| POST /v1/coord/searches/{search_id}/evaluate | 同上 | 200 SearchSnapshot + PolicyDecision |
| GET /v1/coord/searches/{search_id}/candidates | limit、可选 result_cursor | 200 {result_revision, items, next_result_cursor} |
| POST /v1/coord/searches/{search_id}/route-plan | If-Match 与 Idempotency-Key；key、probe: boolean | 200 RoutePlan；不创建任务 |

If-Match 使用带双引号的十进制控制 revision；除创建外的变更操作缺失则返回 428，和当前版本不符返回 **409 REV_CONFLICT**，附 current_revision 且不改变状态。先检查幂等重放，再检查版本：同 command_id、同搜索、同动作、同内容的重传返回原结果，即使当前 revision 已增加；同 id 换内容返回 409 IDEMPOTENCY_CONFLICT。创建请求的 command_id 在本节点创建命令范围内唯一，重传不能新建第二个 search_id。幂等记录至少保留到会话失效，改变状态与记下命令结果必须一致提交。resume 是同 search_id 下新一轮，不能拿远端 next_cursor 充当本机 result_cursor。result_cursor 绑定 search_id、result_revision、排序及页大小；结果版本改变时返回 409 RESULT_CHANGED，从第一页重新读，不丢已存候选。

状态操作约束：pause 使 RUNNING 进入 PAUSED，已暂停可原样返回；resume 只从 SATISFIED、PAUSED、BUDGET_REACHED、FRONTIER_EXHAUSTED 或重新获得入口的 ISOLATED 开启新轮次。RUNNING 不允许用新命令再次 resume，CANCELLED/EXPIRED 必须创建新搜索。cancel 可结束非终止会话，已取消可原样返回。非法状态转换返回 409 STATE_CONFLICT；过期会话返回 410 SESSION_EXPIRED。结果为空不是状态错误。

## 4. 公共 HTTP 与 v1 封套

| 路径（全部 POST） | 操作 | 结果边界 |
|---|---|---|
| /public/v1/coord/hello | HELLO | 验证对方 DID、公钥、版本与 CoordRoute；返回本机 NodeRecord |
| /public/v1/coord/find | FIND | 一页公开 OfferHint、Referral 及远端 next_cursor，不替请求方递归 |
| /public/v1/coord/card | GET_CARD | 精确 provider_did/service_id/card_hash 的原始已签名 Card |
| /public/v1/coord/routes | RESOLVE_ROUTES | 指定商品的有来源 RouteDescriptor 线索 |
| /public/v1/coord/probe | PROBE | nonce 回显及控制口状态；不执行 Agent |
| /public/v1/coord/forward | FORWARD_COORD | 沿 A 明确指定的有界路径，将一条协调请求交给唯一目标 |

这些路径可复用现有允许公开的 **/public/** 入站及反向代理，但须单独实施新路由、认证、限流与协调能力声明。不得借用 /v1/runtime、账户、旧任务中继或业务 A2A 入口当协调接口。私有 NAT 节点可通过有租约的 coord_mailbox 出站会话回答；任务中继仅支持业务任务，不能冒称支持 FIND。

六项逻辑操作均属新协调协议的基础接口；没有已知 Card 或可达转送目标时返回明确状态，不能删去接口冒称完整支持。旧节点作为兼容来源另行标记。

### 4.1 操作体的必要字段

以下表中的公开对象采用第 1 节类型。字段默认必需，标为可选的字段可省略；响应均包在第 4.2 节签名封套内。

| 操作 | 请求 body | 成功响应 body |
|---|---|---|
| HELLO | versions: string[]；node_record: NodeRecord；nonce: string | selected_version: string；node_record: NodeRecord；nonce: 原样回显；limits: object（接受大小和速率限制） |
| FIND | skill: string；coarse_requirements: object；page_size: integer；cursor: string 或 null；query_fingerprint: string；max_response_bytes: integer | query_fingerprint；snapshot_id: string；snapshot_revision: integer；offers: OfferHint[]；referrals: Referral[]；next_cursor: string 或 null；truncated: boolean；limit_reason: string 或 null |
| GET_CARD | key: CandidateKey；card_hash: string | key；card: object；card_hash；served_at: integer；若哈希已失效不能静默返回另一版 |
| RESOLVE_ROUTES | key: CandidateKey；limit: integer | key；routes: RouteDescriptor[]；observed_at: integer；truncated: boolean；无路由时返回空数组 |
| PROBE | nonce: string | nonce；node_did: string；served_at: integer；RTT 由请求者本机计时 |
| FORWARD_COORD | origin_did/target_did: string；parent_request_id: string；inner_request: object；inner_request_hash: string；path: string[]；deadline: integer；max_response_bytes/max_operations: integer | target_response: object 或 null；forward_status: string；路径相关状态与实际消耗；目标响应保持原签名 |

HELLO 中 nonce 与封套 request_id 一起绑定握手，重放旧 NodeRecord 不能被当成本次在线证明。初期 FIND 支持按能力标识查询，required/preferences 中尚无公开映射的条件由本地策略处理；不得自动将整个私有对象上网。

### 4.2 签名封套

所有请求和已认证后的响应使用如下封套：

| 字段 | 类型及约束 |
|---|---|
| v | integer，固定 1 |
| domain | string，固定 a2n-coord/1 |
| type | HELLO、FIND、GET_CARD、RESOLVE_ROUTES、PROBE、FORWARD_COORD 或相应的 *_RESULT / ERROR |
| request_id | string，本条逻辑请求唯一；重传保持原值 |
| in_reply_to | string，仅响应需要，等于请求 request_id |
| sender_did | string，必须由 proof.pub 推导 |
| recipient_did | string，明确绑定预期接收者；响应绑定原请求者。首次无身份线索的 HELLO 可使用 null，取得身份后再次挑战确认；其余请求不得为空 |
| issued_at / expires_at | integer Unix 秒；拒绝过期或显著超前的消息 |
| body | object，操作专属完整业务体 |
| proof | {pub: string, sig: string} |

**签名域**：复制整份封套，且仅移除顶层 proof；用现有 Identity.sign 对该 dict 签名，验签使用 a2n_p2p.envelope.verify_pub，并用现有 did_from_pub/fingerprint_of 检查 sender_did。domain、type、request_id、in_reply_to、时效、body 的每一字段都因此被覆盖。沿用 Identity.sign 的 JSON 序列化：键排序、紧凑分隔符、ensure_ascii=False、UTF-8；实现必须在解析时拒绝重复 JSON key、NaN/Infinity 和不能无损往返的数值。**这是项目现有口径，不声称符合 RFC 8785。** 已认证传输可用于连接身份，但可缓存或转发的响应仍需保留上述独立签名。

NodeRecord 与 Referral 也各自用同一 Identity.sign/verify_pub 原语：分别构造 {domain: "a2n-coord-node/1", value: 对象去掉 proof} 与 {domain: "a2n-coord-referral/1", value: 对象去掉 proof} 后签名。引荐者 DID 必须对应 Referral.proof.pub；target_record 由目标 DID 自签。target_record_hash 为完整已签 NodeRecord 按上述 JSON 字节的 SHA-256 小写十六进制值；Referral 签名覆盖该哈希、完整 target_record、能力提示、观测与过期时间。二次转发不能重签后抹掉原引荐者。OfferHint 由 FIND_RESULT 的应答方签名覆盖，但只属于线索；Card 自证继续调用项目现有 verify_card 与 card_hash，不改 Card 签名规则。

### 4.3 完整 FIND 请求与响应形状

以下是结构完整、**签名与哈希占位值不可用于验签**的例子。FIND 的 query_fingerprint 由公开的 {skill, coarse_requirements, page_size} 按上述项目 JSON 口径求 SHA-256 小写 64 位十六进制；不包含 request_id 或 cursor。私有偏好和总预算没有上网。

~~~json
{
  "v": 1,
  "domain": "a2n-coord/1",
  "type": "FIND",
  "request_id": "req_7e92",
  "sender_did": "did:a2n:ag_aaaaaaaaaaaaaaaaaaaaaaaa",
  "recipient_did": "did:a2n:ag_bbbbbbbbbbbbbbbbbbbbbbbb",
  "issued_at": 1790700000,
  "expires_at": 1790700005,
  "body": {
    "skill": "video-edit",
    "coarse_requirements": {"language": "zh"},
    "page_size": 10,
    "cursor": null,
    "query_fingerprint": "0000000000000000000000000000000000000000000000000000000000000000",
    "max_response_bytes": 65536
  },
  "proof": {"pub": "示例公钥", "sig": "示例签名"}
}
~~~

~~~json
{
  "v": 1,
  "domain": "a2n-coord/1",
  "type": "FIND_RESULT",
  "request_id": "rsp_2b41",
  "in_reply_to": "req_7e92",
  "sender_did": "did:a2n:ag_bbbbbbbbbbbbbbbbbbbbbbbb",
  "recipient_did": "did:a2n:ag_aaaaaaaaaaaaaaaaaaaaaaaa",
  "issued_at": 1790700001,
  "expires_at": 1790700031,
  "body": {
    "query_fingerprint": "0000000000000000000000000000000000000000000000000000000000000000",
    "snapshot_id": "snap_b_31",
    "snapshot_revision": 31,
    "offers": [{
      "provider_did": "did:a2n:ag_eeeeeeeeeeeeeeeeeeeeeeee",
      "service_id": "svc_video_1",
      "skills": ["video-edit"],
      "card_ref": {"provider_did": "did:a2n:ag_eeeeeeeeeeeeeeeeeeeeeeee", "service_id": "svc_video_1"},
      "card_hash": "示例Card哈希",
      "observed_at": 1790699990
    }],
    "referrals": [{
      "introducer_did": "did:a2n:ag_bbbbbbbbbbbbbbbbbbbbbbbb",
      "target_record": {
        "node_did": "did:a2n:ag_cccccccccccccccccccccccc",
        "versions": ["a2n-coord/1"],
        "coord_routes": [{"channel_type": "direct_https", "endpoint": "https://c.example/public/v1/coord"}],
        "operations": ["HELLO", "FIND", "GET_CARD", "RESOLVE_ROUTES", "PROBE", "FORWARD_COORD"],
        "issued_at": 1790699900,
        "expires_at": 1790700200,
        "proof": {"pub": "C的示例公钥", "sig": "C的示例签名"}
      },
      "target_record_hash": "示例NodeRecord哈希",
      "skill_hint": ["video-edit"],
      "observation": "recently_connected",
      "observed_at": 1790699998,
      "expires_at": 1790700058,
      "proof": {"pub": "B的示例公钥", "sig": "B的示例签名"}
    }],
    "next_cursor": "opaque_b_31_10",
    "truncated": false,
    "limit_reason": null
  },
  "proof": {"pub": "B的示例公钥", "sig": "B的响应签名"}
}
~~~

接收方依次检查体积与 JSON 合法性、封套签名及时效、in_reply_to 与公开条件指纹、每份 NodeRecord/Referral 独立签名、Card 引用的安全访问范围。卡未取回并通过供给方验签、DID/service_id/哈希核对之前，OfferHint 只进入待验证集合。FIND_RESULT 中的引荐只增加 A 的 frontier，不触发 B/C 自行递归。

## 5. 分页、额度、幂等与转送

远端 FIND cursor 是 B 签发的不可解释字符串，绑定 B 的 DID、请求者 DID（若为私有会话）、公开条件指纹、page_size、snapshot_id/revision 与有效期。B 返回同一快照的下一页；过期返回 CURSOR_EXPIRED。A 可从该来源第一页重读，靠 CandidateKey、Card 哈希、来源及游标访问记录去重。A 把已验证页、去重集合、next_cursor 和已用额度原子提交；重启不能重置预算。

远端对 (sender_did, type, request_id) 做短期幂等；同 ID、同签名内容重传原响应或返回已处理状态，同 ID 不同内容为 IDEMPOTENCY_CONFLICT。**新逻辑请求使用新 request_id；仅传输层重传原请求保留原 ID。** 即使响应丢失，本地每一次实际发送尝试、收到的字节及验证失败仍受全局与本机限额；不能通过重传免费扩张预算。

FORWARD_COORD 的 inner_request 是 A 签给最终 target_did 的完整封套，inner_request_hash 是其完整签名对象按本项目 JSON 字节计算的 SHA-256；内部操作必须是其余五项之一。A 的原始转送声明绑定 origin_did、target_did、parent_request_id、path、deadline、max_response_bytes/max_operations，转送时必须保留该原始签名声明。path 从 A 的下一跳开始，以目标结束，每个 DID 只能出现一次，最多中间节点数按主规则限制。

优先使用 A→B→C 的一个中间节点；当明确声明的路径更长时，各中间节点只向 A 指定的下一跳转送原声明和内层请求，附自己的认证逐跳封套。逐跳封套只增加当前位置、关联父请求和消耗信息，不能改写原始路径或获得新的额度。下一跳校验原声明签名、当前位置、前一认证邻居、目标授权与时效；不能添加节点、广播、嵌套新的 FORWARD_COORD 或代 A 创建下游 FIND。最终目标仅执行内层一次请求，原始响应保留目标签名，沿请求回路返回。

A 在派发前按路径预留操作、字节与并发额度，B/C 等各自执行本机限流；即使某跳谎报剩余额度，也不能绕过接收节点本机上限。目标无法到达时返回 UNREACHABLE，路径类型不支持时返回 UNSUPPORTED_ROUTE，不伪造空结果。转送幂等按原始请求、最终目标和当前位置绑定。

## 6. 通道方案与业务任务交接

route-plan 返回 {key, plan_revision, choices, preferred_route_id, reason_codes, remaining_budget}。每条 choice 至少有 channel_type、route_card_hash、完整已验 Card、有效期、授权状态及分开的 control_reachable / task_reachable。直连与密封中继的 Card 可有不同哈希，但必须解析到同一个 CandidateKey；sealed_relay_a2a 只能交给支持该协议的适配器，不能作为普通 A2A URL 自动降级。未知任务可达性必须显示 unknown。

业务层选中商品后，以选定 Card 与 route-plan 重新确认报价、试用状态、版本、接单条件和通道有效期，再**独立**创建业务 task_id / 幂等键并调用既有 CallPipeline。任务绑定至少保存 provider_did、service_id、selected_card_hash、channel_type、route_id、transport_route_name、task_id；远端接受后补 remote_task_id、context_id。协调层不拥有交易任务。执行中更新的 route-plan 只作用于新任务；旧任务状态查询与取消固定原通道。仅已确认请求未送达时可尝试其他同等授权通道；TIMEOUT 或 DELIVERY_UNKNOWN 保留未知状态并先沿原通道查任务，不跨候选重发。签名、权限、协议失败不得降级到更弱通道。

## 7. 错误与 HTTP 映射

公共协议错误在可认证时返回签名 ERROR 封套，body 至少为 {code, message, retry_after_seconds?, current_revision?}；认证前的非法输入只返回最小 JSON 错误，不泄露节点目录。HTTP 码不取代 body.code。单来源失败不抹掉其他已验证候选。

| HTTP | code | 情形 |
|---|---|---|
| 200 | OK / NO_MATCH_IN_SNAPSHOT | 合法空页也成功，不能解释成全网无供给 |
| 400 | INVALID_REQUEST | 结构、条件、签名域字段或游标形状无效 |
| 401 | UNVERIFIED_IDENTITY | 公钥与 DID 不符或验签失败 |
| 403 | FORBIDDEN_TARGET | 转送目标、地址范围或操作未授权 |
| 404 | NOT_FOUND | 精确 Card 或本地 search_id 不存在 |
| 409 | REV_CONFLICT / RESULT_CHANGED / IDEMPOTENCY_CONFLICT / STATE_CONFLICT | 本地并发版本、请求重用或状态转换冲突 |
| 410 | CURSOR_EXPIRED / SESSION_EXPIRED | 远端快照／游标或本地会话失效 |
| 413 | REQUEST_TOO_LARGE / RESPONSE_TOO_LARGE | 请求或响应超过声明与本机上限 |
| 422 | UNSUPPORTED_ROUTE | 声明的路径或通道类型不受支持 |
| 426 | UNSUPPORTED_VERSION | 无共同协调版本；保留旧来源能力 |
| 428 | REV_REQUIRED | 本地变更缺少 If-Match |
| 429 | RATE_LIMITED | 按身份／连接限流，附 Retry-After |
| 502 | UNREACHABLE | 明确目标转送失败，不能伪造空页 |
| 503 | BUSY | 节点当前容量不足，附建议退避 |
| 504 | DEADLINE_EXCEEDED | 截止时间到，保留已有部分结果 |

## 8. 兼容与最小验收

现有 a2n_sdk.ports.DiscoveryPort 的 advertise/discover/snapshot 可包装成**兼容叶子来源**：返回已验签 Card，但没有 Referral、远端分页和公共协调能力；旧 /public/v1/agents 也如此。旧 P2P 种子与手工公共目录可作为初始 frontier。HELLO 不支持 a2n-coord/1 的节点只标记能力缺失，不扣业务信誉；原直连/中继调用仍按原协议。新公共基本发现与见证、任务中继等可选服务要拆开，不直接把旧 public-service 开关统一设为 true。

首批契约验收至少覆盖：A→B→C 分页发现与引荐验签、A→B→A 防环、重复 request_id 与修改内容冲突、跨页重启及游标失效不重置额度、两个买方不同本地策略、NAT C 通过专用 coord_mailbox 实时答复或明确缓存状态、恶意地址不触达内网、控制口探测不执行 Agent、直连与中继 Card 绑定、TIMEOUT 后不因新通道重复执行。验收输出须区分进程内、本机容器及不同公网环境，不宣称全网最短或完全发现。
