# 错误码登记表

> 本文件由 `python scripts/error_codes.py --write` 生成，不要手改。
> 校验：`python scripts/error_codes.py`（有中文 `raise` 没带码就失败）。

共 **416** 个码，覆盖 **494** 处消息（`raise` 与已带码的响应字段）。

## 约定

- 消息形如 `"CODE: 中文说明"`；码是大写蛇形，**永不翻译**，说明随语言变。
- 前端 `I18N.errorText()` 按码取译：默认语言原样返回，其它语言查 `err.<CODE>`。
- 同一个码可以出现在多处，但语义必须一致；「次数」列是出现处数。

## A2A

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `A2A_GATEWAY_NOT_STARTED` | 本地 A2A 网关尚未启动 | 1 |
| `A2A_POLL_INTERVAL_INVALID` | A2A 轮询间隔不能小于 0 | 1 |
| `A2A_TIMEOUT_INVALID` | A2A 超时必须大于 0 | 1 |
| `A2A_URL_INVALID` | A2A 地址必须是 http:// 或 https:// | 1 |

## ACCOUNT

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `ACCOUNT_ID_REQUIRED` | account_id 不能为空 | 2 |
| `ACCOUNT_NOT_FOUND` | 账户不存在：{account_id} | 2 |
| `ACCOUNT_STILL_IN_USE` | 账户仍被 Agent 使用：{names}；请先移除这些资源 | 2 |

## ADAPTER

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `ADAPTER_ROUTE_DUPLICATE` | 网络路径名称不能重复 | 1 |
| `ADAPTER_ROUTE_REQUIRED` | 至少需要一条网络路径 | 1 |

## ADMITTED

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `ADMITTED_AGENT_CHANGED` | 接单后商品条件变化，未执行 | 1 |

## AGENT

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `AGENT_ID_INVALID` | Agent 标识只支持 1–128 位字母、数字、下划线和连字符 | 1 |
| `AGENT_NOT_FOUND` | Agent 不存在 | 1 |

## AGGREGATE

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `AGGREGATE_EMPTY_CANDIDATES` | 聚合至少要有一个候选 agent | 1 |

## AMOUNT

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `AMOUNT_NOT_FINITE` | 金额必须是有限数字 | 1 |

## API

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `API_BODY_INVALID` | 请求体为空或过大 | 1 |
| `API_BODY_NOT_OBJECT` | 请求必须是 JSON 对象 | 1 |
| `API_JSON_REQUIRED` | 请使用 application/json | 1 |

## AUTOSTART

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `AUTOSTART_UNSUPPORTED_PLATFORM` | 桌面自启动当前支持 Windows；Linux 服务器由完整 SDK 的 systemd 安装器管理 | 1 |

## BACKUP

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `BACKUP_CONTENT_INVALID` | 备份内容体积或编码异常 | 1 |
| `BACKUP_CONTENT_NOT_NODE` | 备份内容不符合节点格式 | 1 |
| `BACKUP_CORRUPT` | 备份文件损坏 | 2 |
| `BACKUP_DB_KEY_INVALID` | 备份数据库密钥参数损坏 | 1 |
| `BACKUP_FILE_INVALID` | 备份文件不存在或体积不合范围 | 1 |
| `BACKUP_FORMAT_UNSUPPORTED` | 不支持的备份格式 | 1 |
| `BACKUP_HEADER_CORRUPT` | 备份头部损坏 | 1 |
| `BACKUP_INCOMPLETE` | 备份文件不完整 | 1 |
| `BACKUP_KDF_CORRUPT` | 备份加密参数损坏 | 1 |
| `BACKUP_KDF_UNSUPPORTED` | 不支持的备份加密参数 | 1 |
| `BACKUP_MANIFEST_INVALID` | 备份说明体积异常 | 1 |
| `BACKUP_NETWORK_INVALID` | 网络配置体积异常 | 1 |
| `BACKUP_TOO_LARGE` | 备份体积超过当前导出上限 | 1 |
| `BACKUP_VERIFY_FAILED` | 备份身份或数据库校验失败 | 1 |

## BLOCKED

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `BLOCKED_NOT_BOOLEAN` | blocked 必须为布尔值 | 1 |

## BOOTSTRAP

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `BOOTSTRAP_NOT_HOST_PORT` | bootstrap 必须是 host:port：{value!r} | 1 |
| `BOOTSTRAP_PORT_INVALID` | bootstrap 端口不合法：{value!r} | 1 |

## BUSY

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `BUSY` | 协调邮箱名额已满 | 2 |

## CALL

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `CALL_NOT_LOCAL` | 这条记录既不是本机待使用（买方），也不是本机供给（卖方） | 1 |
| `CALL_RECORD_NOT_FOUND` | 本地没有这条调用记录 | 1 |
| `CALL_TARGET_REQUIRED` | 必须指明是哪一笔（scope + task_id） | 2 |

## CALLS

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `CALLS_CANCELER_BAD_OUTCOME` | 远端取消器没有返回 CallOutcome | 1 |
| `CALLS_CANCELER_CHANGED_TASK_ID` | 远端取消器改变了本机 task id | 1 |
| `CALLS_CREDENTIAL_BAD_OUTCOME` | 调用凭证处理器没有返回 CallOutcome | 1 |
| `CALLS_EXECUTOR_BAD_OUTCOME` | 调用执行体没有返回 CallOutcome | 1 |
| `CALLS_QUEUE_FULL` | 节点任务队列已满，请稍后再试 | 1 |
| `CALLS_REFRESHER_BAD_OUTCOME` | 远端任务刷新器没有返回 CallOutcome | 1 |
| `CALLS_REFRESHER_CHANGED_TASK_ID` | 远端任务刷新器改变了本机 task id | 1 |
| `CALLS_STOPPING` | 节点正在停止 | 1 |

## CAPACITY

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `CAPACITY_LIMIT` | 免费在途任务已达到上限 | 1 |

## CARD

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `CARD_DISCOVER_LIMIT_INVALID` | x-a2n.discover_limit 必须是非负整数（0=不限）：{dl!r} | 1 |
| `CARD_EXT_NOT_OBJECT` | x-a2n 必须是对象 | 1 |
| `CARD_MISSING_FIELD` | Agent Card 缺少必填字段: {field_name} | 1 |
| `CARD_NAME_INVALID` | name 必须是非空字符串 | 1 |
| `CARD_NOT_OBJECT` | Agent Card 必须是 JSON 对象 | 1 |
| `CARD_PRICE_AMOUNT_INVALID` | 价目条目 amount 必须为非负整数（0 为免费）：{e!r} | 1 |
| `CARD_PRICE_BOOK_CURRENCY_INVALID` | price_book[{skill!r}] 必须是 {{币种: 条目}} | 1 |
| `CARD_PRICE_BOOK_INVALID` | price_book 必须是 {技能: {币种: 条目}} 结构 | 1 |
| `CARD_PRICE_DIMENSIONS_MISSING` | price_book[{skill!r}][{cur}] 缺 dimensions | 1 |
| `CARD_PRICE_DIMENSION_KEY_MISSING` | 计量维度缺 key：{d!r} | 1 |
| `CARD_PRICE_INVALID` | 价目不合法：{skill}/{cur}={v!r} | 1 |
| `CARD_PRICE_KEY_MISSING` | 价目条目缺 key：{e!r} | 1 |
| `CARD_PRICE_PER_INVALID` | 价目条目 per 必须为正整数：{e!r} | 1 |
| `CARD_SKILLS_EMPTY` | skills 必须是非空数组 | 1 |
| `CARD_SKILLS_REQUIRED` | 至少一条技能 | 1 |
| `CARD_SKILL_ID_EMPTY` | 每条技能都要有非空 id：{s!r} | 1 |
| `CARD_SKILL_ID_REQUIRED` | 每条技能都要有 id | 1 |
| `CARD_TRIAL_OPT_OUT_FORBIDDEN` | 不允许在卡上退出免费期（x-a2n.trial=false）：想被网络发现，前 10 次完成的调用必须免费服务，额度用尽毕业之后才可收费 | 1 |
| `CARD_UID_INVALID` | uid 必须是 UUID 格式：{ext['uid']!r} | 1 |
| `CARD_URL_INVALID` | url 必须是 http(s) 地址或留空（由本节点生成公共投影）：{url!r} | 1 |

## CHANNEL

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `CHANNEL_PLAN_FOREIGN_GOODS` | 通道方案混入了其他商品 | 1 |

## CLI

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `CLI_LANG_UNSUPPORTED` | 不支持的语言：{picked}（可选：{choices}） | 1 |

## CLIENT

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `CLIENT_NOT_LOOPBACK` | NodeClient 必须连接本机 HTTP 回环入口 | 1 |
| `CLIENT_PATH_INVALID` | 请求路径必须是本机绝对路径 | 1 |

## COORD

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `COORD_ADDRESS_INVALID` | 协调地址无效 | 1 |
| `COORD_ADDRESS_UNPARSABLE` | 无法解析协调地址 | 1 |
| `COORD_AUTO_CONTINUE_NEEDS_BUDGET` | auto_continue 打开时必须给出 total_budget（防无限续查） | 1 |
| `COORD_BUDGET_EXCEEDED` | 已用额度 {name} 超过授权上限 | 1 |
| `COORD_BUDGET_FIELD_UNKNOWN` | 未知额度字段：{name} | 2 |
| `COORD_BUDGET_INVALID` | Budget.{name} 必须是正整数，收到 {value!r} | 1 |
| `COORD_BUDGET_NON_NEGATIVE` | {name} 必须是非负整数，收到 {value!r} | 1 |
| `COORD_BUSINESS_CHANNEL_UNKNOWN` | 未知业务通道类型：{self.channel_type} | 1 |
| `COORD_CANDIDATE_KEY_INVALID` | CandidateKey 的 provider_did 与 service_id 都不可为空 | 1 |
| `COORD_CARD_HINT_MISMATCH` | 商品卡与发现线索不一致 | 1 |
| `COORD_CARD_META_HTTP_ERROR` | 商品卡元数据返回 HTTP {status} | 1 |
| `COORD_CARD_SIGNATURE_INVALID` | 商品卡签名无效 | 1 |
| `COORD_CHALLENGE_INVALID` | 协调握手挑战无效 | 1 |
| `COORD_CHANNEL_EXPIRED` | 候选的通道声明已过期，请续查 | 1 |
| `COORD_CHANNEL_FOREIGN` | 所选通道不属于该候选 | 1 |
| `COORD_CHANNEL_TYPE_UNKNOWN` | 未知协调通道类型：{self.channel_type} | 1 |
| `COORD_CURSOR_FOREIGN` | 游标不属于本次查询 | 1 |
| `COORD_CURSOR_OFFSET_INVALID` | 游标偏移无效 | 1 |
| `COORD_DECISION_UNKNOWN` | 未知策略结论：{self.decision} | 1 |
| `COORD_DIRECTORY_TOO_LARGE` | 兼容目录响应过大 | 1 |
| `COORD_DISCOVERY_OVER_BUDGET` | 兼容发现响应超过本轮额度 | 1 |
| `COORD_DISCOVERY_RESPONSE_MISMATCH` | 发现应答与查询不一致 | 1 |
| `COORD_ENDPOINT_NOT_FOUND` | 协调接口不存在 | 1 |
| `COORD_ENDPOINT_REQUIRED` | CoordRoute.endpoint 不可为空 | 1 |
| `COORD_ENVELOPE_NOT_OBJECT` | 协调报文顶层必须是对象 | 1 |
| `COORD_FINGERPRINT_MISMATCH` | query_fingerprint 不一致 | 1 |
| `COORD_FORWARD_INVALID` | 转送约定无效；只支持明确指定的一个邻居 | 1 |
| `COORD_HANDSHAKE_MISMATCH` | 首次协调握手不匹配 | 1 |
| `COORD_HTTP_ERROR` | 协调节点返回 HTTP {status} | 2 |
| `COORD_IDENTITY_VERSION_MISMATCH` | 节点身份或协议版本不匹配 | 1 |
| `COORD_MAILBOX_IDENTITY_INVALID` | 协调邮箱身份无效 | 1 |
| `COORD_MAILBOX_LEASE_EXPIRED` | 协调邮箱租约已过期 | 1 |
| `COORD_MAILBOX_NOT_BOUND` | 邮箱通道没有绑定本节点 | 1 |
| `COORD_MAILBOX_OPERATION_UNKNOWN` | 未知协调邮箱操作 | 1 |
| `COORD_MAILBOX_RECORD_INVALID` | 协调邮箱节点记录无效 | 1 |
| `COORD_MAILBOX_REPLY_MISMATCH` | 邮箱应答与原请求不一致 | 1 |
| `COORD_MAILBOX_TYPE_MISMATCH` | 协调邮箱消息类型与操作不一致 | 1 |
| `COORD_MESSAGE_TYPE_UNKNOWN` | 未知协调消息类型：{type} | 1 |
| `COORD_MIN_CANDIDATES_INVALID` | min_candidates 必须是 1–256 的整数 | 1 |
| `COORD_NODE_DID_REQUIRED` | NodeRecord.node_did 不可为空 | 1 |
| `COORD_NODE_RECORD_SIGNATURE_INVALID` | 节点记录签名无效或已过期 | 1 |
| `COORD_OPERATION_UNKNOWN` | 未知协调操作 | 2 |
| `COORD_OPERATION_UNSUPPORTED` | 不支持的协调操作 | 1 |
| `COORD_PAGE_SIZE_INVALID` | page_size 必须是正整数 | 2 |
| `COORD_PATH_OPERATION_MISMATCH` | 协调路径与操作不一致 | 1 |
| `COORD_PREFERENCES_INVALID` | required 和 preferences 必须是对象 | 1 |
| `COORD_PROBE_NOT_BOOLEAN` | probe 必须是布尔值 | 1 |
| `COORD_PROVIDER_DID_REQUIRED` | provider_did 不可为空 | 1 |
| `COORD_PROVIDER_MISMATCH` | 商品所属节点与提供方不一致 | 1 |
| `COORD_REFERRAL_PRIVATE_TARGET` | 引荐不能授予访问回环或内网地址的权限；请明确配置可信网络 | 1 |
| `COORD_REFERRAL_RECORD_UNSIGNED` | 引荐的 target_record 未通过自签校验，不能引荐 | 1 |
| `COORD_REFERRAL_SELF` | Referral 的引荐者与目标不能是同一节点 | 1 |
| `COORD_REFERRER_REQUIRED` | Referral.introducer_did 不可为空 | 1 |
| `COORD_REQUEST_ID_INVALID` | request_id 无效 | 1 |
| `COORD_REQUEST_UNVERIFIED` | 协调请求身份或签名无效 | 1 |
| `COORD_RESPONSE_UNVERIFIED` | 协调响应身份、签名或请求关联无效 | 1 |
| `COORD_ROUTE_ID_REQUIRED` | RouteDescriptor.route_id 不可为空 | 1 |
| `COORD_SEARCH_SKILL_REQUIRED` | SearchSpec.skill 不可为空 | 1 |
| `COORD_SEARCH_STATE_UNKNOWN` | 未知搜索状态：{self.state} | 1 |
| `COORD_SERVICE_ABSENT` | 没有公共协调服务 | 1 |
| `COORD_SERVICE_UNAVAILABLE` | 协调服务不可用 | 1 |
| `COORD_SKILL_INVALID` | 能力标识无效 | 1 |
| `COORD_SKILL_TOO_LONG` | 能力标识最多 96 个字符 | 1 |
| `COORD_SPECIAL_ADDRESS_FORBIDDEN` | 禁止访问特殊网络地址 | 1 |
| `COORD_TARGET_DECLARATION_INVALID` | 目标节点声明无效或没有协调通道 | 1 |
| `COORD_VERIFICATION_STATE_UNKNOWN` | 未知核验状态：{self.verification} | 1 |
| `COORD_VERSIONS_REQUIRED` | NodeRecord.versions 不可为空 | 1 |

## CROSS

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `CROSS_NETWORK_NOT_LOCAL_CONSOLE` | {self.base}/console 没回 A2N_LOCAL_TOKEN —— 这个口是不是反代口？本机节点请用回环管理口（默认 8890），不是对外反代口。 | 1 |

## DEADLINE

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `DEADLINE_EXCEEDED` | 协调邮箱目标未及时应答 | 1 |

## DECISION

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `DECISION_ALREADY_FIXED` | 修改方案须提出新方案 | 2 |

## DELIVERED

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `DELIVERED_CALL_REQUIRES_SETTLEMENT_ONLY_CHANNEL` | 已交付任务只允许补付款，不再执行 | 1 |

## DESKTOP

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `DESKTOP_INSTALL_CONFLICT` | 安装目录已有不同内容的同名程序 | 1 |
| `DESKTOP_INSTALL_IN_PROGRESS` | 安装已进行或参数不正确 | 1 |
| `DESKTOP_INSTALL_PAYLOAD_INVALID` | 安装参数长度不正确 | 1 |
| `DESKTOP_INTERNAL_ENTRY_UNSUPPORTED` | 不支持的内部运行入口 | 1 |
| `DESKTOP_RESTORE_ARGS_INVALID` | 恢复参数不正确 | 1 |

## DIMENSION

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `DIMENSION_DEFINITION_INVALID` | 维度定义必须是 dict 且含 key 字段 | 1 |
| `DIMENSION_KEY_MISSING` | 维度缺少 key | 1 |

## DISCOVERY

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `DISCOVERY_CHANNELS_MISSING` | 节点尚未配置任何发现通道；仍可直接粘贴 Agent Card | 1 |
| `DISCOVERY_SKILL_REQUIRED` | 发现技能不能为空 | 1 |

## DISPUTE

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `DISPUTE_CALL_NOT_FOUND` | 本机没有这条调用记录，无法对它说不认 | 1 |
| `DISPUTE_NOT_FOUND` | 异议不存在 | 2 |
| `DISPUTE_REASON_REQUIRED` | 必须写清为什么不认（哪怕一句话） | 1 |
| `DISPUTE_REASON_TOO_LONG` | 理由不超过 {MAX_REASON} 字 | 1 |
| `DISPUTE_SIDE_INVALID` | 申诉方只能是 {SIDES} 之一 | 1 |
| `DISPUTE_TARGET_REQUIRED` | 必须指明是哪一笔（scope + task_id） | 1 |

## ENABLED

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `ENABLED_NOT_BOOLEAN` | enabled 必须是布尔值 | 2 |

## EXECUTOR

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `EXECUTOR_STOPPED` | 执行器已经停止 | 1 |
| `EXECUTOR_WORKERS_INVALID` | max_workers 必须大于 0 | 1 |

## EXPERIENCE

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `EXPERIENCE_BODY_NOT_OBJECT` | 请求正文必须为对象 | 1 |
| `EXPERIENCE_LIMIT_INVALID` | limit 必须是 1–50 的整数 | 1 |
| `EXPERIENCE_NOTE_TOO_LONG` | 公开短评不能超过 500 字 | 1 |
| `EXPERIENCE_PUBLISH_FOREIGN_FEEDBACK` | 只能发布本机作者的有效签名反馈 | 1 |
| `EXPERIENCE_QUERY_NOT_FOUND` | 体验查询不存在 | 1 |
| `EXPERIENCE_QUERY_NOT_OBJECT` | 查询主体必须是对象 | 1 |
| `EXPERIENCE_QUERY_SUBJECT_REQUIRED` | 查询主体必须明确绑定商品、卖方或买方 | 1 |
| `EXPERIENCE_QUOTA_INVALID` | 体验查询额度必须是有界正整数 | 1 |
| `EXPERIENCE_QUOTA_UNKNOWN` | 未知体验查询额度 | 1 |
| `EXPERIENCE_SAMPLE_FOREIGN_GOODS` | 本节点只提供自己商品的样品 | 1 |
| `EXPERIENCE_VISIBILITY_INVALID` | 公开范围只能是 PUBLIC / PARTIES_ONLY | 1 |

## FEEDBACK

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `FEEDBACK_ALREADY_EXISTS` | 这个方向已经有一份反馈了；要修改请用「改反馈」 | 1 |
| `FEEDBACK_CALL_NOT_FOUND` | 本机没有这条调用记录，无法对它写反馈 | 2 |
| `FEEDBACK_DIMENSIONS_NOT_OBJECT` | dimensions 必须是对象 | 1 |
| `FEEDBACK_DIMENSION_UNSUPPORTED` | {direction} 不支持维度 {k}；可用：{'、'.join(allowed)} | 1 |
| `FEEDBACK_DIRECTION_INVALID` | 反馈方向只能是 {' / '.join(DIRECTIONS)} | 2 |
| `FEEDBACK_EMPTY` | 反馈至少要有维度分或一句原因，空反馈没有意义 | 2 |
| `FEEDBACK_MISSING_FIELDS` | 反馈缺少 task_id 或 author_did | 1 |
| `FEEDBACK_NOTE_TOO_LONG` | 原因不超过 {MAX_NOTE} 字 | 1 |
| `FEEDBACK_NOT_FOUND` | 没有这份反馈 | 2 |
| `FEEDBACK_NOT_OBJECT` | 反馈必须是对象 | 1 |
| `FEEDBACK_QUALITY_NOT_DELIVERED` | 这一单没有完成交付，不能评价成品质量 | 2 |
| `FEEDBACK_RECARRY_DISABLED` | 本节点未启用反馈补交 | 1 |
| `FEEDBACK_SCORE_INVALID` | 维度 {k} 的分数必须是 1–5 的整数 | 1 |
| `FEEDBACK_TASK_ID_REQUIRED` | 必须指明是哪一笔任务（task_id） | 1 |

## FILE

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `FILE_TRANSFER_DRIVER_MISSING` | 当前没有文件转送驱动 | 1 |

## IDEMPOTENCY

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `IDEMPOTENCY_KEY_REQUIRED` | 必须提供 Idempotency-Key | 4 |

## IDENTITY

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `IDENTITY_VERIFY_USE_ENVELOPE` | 跨节点验签请用 envelope.verify_envelope | 1 |

## JSON

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `JSON_DUPLICATE_KEY` | 重复的 JSON 键：{key} | 2 |
| `JSON_NON_FINITE` | 不接受非有限数值：{name} | 2 |
| `JSON_NOT_OBJECT` | 请求必须是 JSON 对象 | 1 |

## LISTED

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `LISTED_NOT_BOOLEAN` | listed 必须是布尔值 | 2 |

## LOCAL

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `LOCAL_ADMIN_CREDENTIAL_REQUIRED` | 需要本机管理凭据 | 13 |
| `LOCAL_ADMISSION_POLICY` | 本机当前不自动接此买方的任务 | 1 |
| `LOCAL_CONSOLE_LOOPBACK_ONLY` | 管理页只在本机开放 | 2 |
| `LOCAL_CONSOLE_ORIGIN_FORBIDDEN` | 控制台来源不被允许 | 1 |
| `LOCAL_CONSOLE_OR_PAIRING_REQUIRED` | 请从本机控制台打开，或先完成远程管理配对 | 5 |
| `LOCAL_CONSOLE_REQUIRED` | 请从本机控制台打开 | 1 |
| `LOCAL_FULL_NODE_MISSING` | 完整节点未安装 | 1 |
| `LOCAL_GATEWAY_ACCESS_CLOSED` | 本机网关没有开放此访问 | 2 |
| `LOCAL_NONPERSISTENT_NO_CALLS` | 非持久模式没有本地调用记录 | 1 |
| `LOCAL_NONPERSISTENT_NO_DISPUTE` | 非持久模式没有本地不认记录 | 1 |
| `LOCAL_NONPERSISTENT_NO_FEEDBACK` | 非持久模式没有本地反馈记录 | 1 |
| `LOCAL_PAIRING_REQUIRED` | 请先通过本机或已配对的管理接口访问 | 2 |
| `LOCAL_POLICY_ABSENT` | 没有本地策略 | 1 |
| `LOCAL_PUBLIC_DIRECTORY_ABSENT` | 没有公共目录 | 1 |
| `LOCAL_PUBLIC_SERVICE_ABSENT` | 没有公共服务 | 2 |
| `LOCAL_SELECTION_POLICY` | 本机当前不自动调用此供给 | 1 |

## MANAGEMENT

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `MANAGEMENT_OPERATION_UNSUPPORTED` | 不支持的本机管理操作 | 1 |

## MEDIUM

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `MEDIUM_CODE_MISSING` | 媒介缺少 code | 1 |
| `MEDIUM_DEFINITION_INVALID` | 媒介定义必须是 dict 且含 code/currency/exponent 字段 | 1 |
| `MEDIUM_FIELDS_MISSING` | 媒介 {code} 缺少 currency 或 exponent | 1 |
| `MEDIUM_UNKNOWN` | 未注册的媒介：{code}（已注册：{sorted(MEDIA)}） | 1 |

## MERKLE

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `MERKLE_LEAF_INDEX_OUT_OF_RANGE` | 叶子下标越界：{index} / {len(level)} | 1 |

## MOUNT

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `MOUNT_NOT_FOUND` | 挂载不存在 | 7 |

## NO

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `NO_PROBE_ADDRESS` | 没有可探测的 HTTP 地址 | 1 |

## NODE

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `NODE_ADDRESS_HOST_REQUIRED` | 节点地址缺少主机名 | 1 |
| `NODE_ADDRESS_IPV6_BRACKET` | IPv6 地址缺少右方括号：{text} | 1 |
| `NODE_ADDRESS_PORT_REQUIRED` | 地址必须带端口：host:port | 1 |
| `NODE_ADDRESS_REQUIRED` | 节点地址不能为空 | 3 |
| `NODE_DID_REQUIRED` | node_did 不能为空 | 1 |
| `NODE_EXIT_UNCONFIRMED_CANCEL` | 节点在取消请求尚未确认时退出；远端结果未知，未自动重放 | 1 |
| `NODE_HOME_IN_USE` | 这个节点目录已经有运行中的实例 | 1 |
| `NODE_PORT_NOT_NUMERIC` | 端口不是数字：{port_text!r} | 1 |
| `NODE_PORT_OUT_OF_RANGE` | 端口必须在 1–65535 之间 | 1 |

## NOT

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `NOT_FOUND` | 没有这个版本的商品卡 | 1 |

## ORIGINAL

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `ORIGINAL_CARD_VERIFY_FAILED` | 原始卡片验签失败：{reason} | 1 |

## P2P

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `P2P_ADVERT_INDEX_TOO_LARGE` | P2P 卡片索引为 {size} 字节，超过安全上限 {MAX_ADVERT_BYTES}；请拆分到不同节点，完整卡片不会走 gossip | 1 |
| `P2P_BOOTSTRAP_INVALID` | bootstrap 地址不合法：{addr!r} | 1 |
| `P2P_CARD_ENDPOINT_PORT_INVALID` | 卡片地址端口不合法：{value!r} | 1 |
| `P2P_CARD_ENDPOINT_UNFETCHABLE` | 卡片地址不是可拉取的 HTTP(S) 入口：{value!r} | 1 |
| `P2P_CARD_FETCH_TIMEOUT_INVALID` | 卡片拉取超时必须大于 0 | 1 |
| `P2P_CARD_FOREIGN_OWNER` | 不能替其他节点通告卡片：{owner!r} | 1 |
| `P2P_CARD_NOT_ATTESTED` | 不能通告未通过自证的 Agent Card：{reason} | 1 |
| `P2P_CARD_NO_SKILLS` | Agent Card 没有可通告的技能 | 1 |
| `P2P_CARD_SERVICE_ID_INVALID` | Agent Card 的供给标识为空或过长 | 1 |
| `P2P_DID_INVALID` | 节点 DID 不合法 | 1 |
| `P2P_DISCOVERY_BUDGET_TOO_SMALL` | 兼容发现字节额度不足 | 1 |
| `P2P_DISCOVERY_DISABLED` | 节点未启用 P2P 发现 | 1 |
| `P2P_DISCOVERY_SKILL_REQUIRED` | 发现技能不能为空 | 1 |
| `P2P_DISCOVERY_TIMEOUT_INVALID` | 发现超时必须大于 0 | 1 |
| `P2P_ENTRY_PORT_INVALID` | P2P 对外 HTTP 入口端口不合法 | 1 |
| `P2P_ENTRY_URL_INVALID` | P2P 对外 HTTP 入口必须是无账号、查询参数和片段的 http(s) URL | 1 |
| `P2P_GOSSIP_TOO_LARGE` | P2P 签名发现包为 {size} 字节，超过安全上限 {MAX_GOSSIP_BYTES}；请缩短能力标识或入口地址 | 1 |
| `P2P_IDENTITY_TYPE_INVALID` | identity 必须是 a2n_p2p.Identity | 1 |
| `P2P_PEER_UNKNOWN` | 邻居不存在或已经离线 | 1 |
| `P2P_PORT_INVALID` | P2P UDP 端口必须在 1–65535 之间 | 1 |
| `P2P_PROBE_INTERVAL_INVALID` | 邻居探测间隔和超时必须大于 0 | 1 |
| `P2P_PROBE_TIMEOUT_INVALID` | 探测超时必须大于 0 | 1 |
| `P2P_PROJECTION_NOT_SUPPLY` | 工作台消费投影不能作为本节点供给重新通告 | 1 |
| `P2P_PUNCH_TIMEOUT_INVALID` | 打洞超时必须大于 0 | 1 |
| `P2P_ROUTE_OBSERVE_DISABLED` | 本节点未启用 P2P 路由观察 | 1 |
| `P2P_SEED_ADDRESS_INVALID` | 种子地址不合法：{addr!r} | 1 |
| `P2P_SEED_FORMAT_INVALID` | P2P 种子必须写成 host:port（例如 127.0.0.1:9701） | 1 |
| `P2P_SKILL_ID_INVALID` | 能力标识必须为 1–96 个字符 | 1 |
| `P2P_SUPPLY_ID_DUPLICATE` | 供给标识重复：{sid} | 1 |
| `P2P_TARGET_DID_INVALID` | 目标 DID 不合法 | 1 |
| `P2P_TARGET_DID_REQUIRED` | 目标 DID 不能为空 | 1 |
| `P2P_TOO_MANY_LOCAL_CARDS` | 单节点最多通告 {MAX_LOCAL_CARDS} 张 Agent Card | 1 |

## PAIRING

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `PAIRING_ACK_INVALID` | ack 必须是签名回执 | 1 |
| `PAIRING_CODE_EXPIRED` | 配对码已失效，请在节点终端生成新码 | 1 |
| `PAIRING_CODE_WRONG` | 配对码不正确 | 1 |
| `PAIRING_LOCAL_ONLY` | 配对只允许本机访问 | 1 |
| `PAIRING_ORIGIN_NOT_ALLOWED` | 此控制台来源未获允许 | 1 |

## PAYMENT

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `PAYMENT_COORDINATION_INPUT_TOO_LARGE` | 单次输入上限 64 KiB | 1 |
| `PAYMENT_ORDER_LOCKED` | 不允许为同一计划换付款授权 | 1 |
| `PAYMENT_UNAVAILABLE` | 免费条件不能授权收费 | 2 |

## PEER

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `PEER_RECEIPT_DISABLED` | 节点未启用双边收据 | 1 |
| `PEER_RECEIPT_NOT_FOUND` | 本节点没有这份交付收据 | 1 |
| `PEER_RECEIPT_UNSUPPORTED` | 对方未声明双边收据协议 | 1 |
| `PEER_SIGN_REQUEST_EXPIRED` | 签名请求已过期；请用 tasks/get 查询旧任务 | 1 |
| `PEER_SIGN_REQUEST_INVALID` | 节点签名请求无效：{why} | 2 |
| `PEER_SIGN_REQUEST_MISMATCH` | 节点签名请求与 Agent、任务或能力不一致 | 1 |
| `PEER_SIGN_REQUEST_NOT_OBJECT` | 节点签名请求必须是对象 | 1 |
| `PEER_TASK_CONTROL_UNAUTHORIZED` | 节点任务控制未授权：{why} | 5 |
| `PEER_TASK_METHOD_UNSUPPORTED` | 不支持的节点任务控制方法 | 1 |
| `PEER_WITNESS_INVALID` | 双方哈希见证声明无效 | 1 |

## PORT

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `PORT_TAKEN_BY_OTHER_NODE` | 端口 {port} 已由另一份节点目录使用，请另选 --port | 1 |

## PRICE

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `PRICE_AMOUNT_INVALID` | amount 必须为非负整数 | 3 |
| `PRICE_BUDGET_INVALID` | 预算必须是非负最小单位整数 | 1 |
| `PRICE_CURRENCY_REQUIRED` | 必须明确选择价目表支持的币种；未标价不能推断免费 | 1 |
| `PRICE_HINT_UNIT_INVALID` | 旧 price_hint 必须明确为 fen_per_call；不能把积分或未知单位解释为 CNY | 1 |
| `PRICE_METER_INVALID` | 计量必须是有限非负数 | 2 |
| `PRICE_METER_NOT_BOOLEAN` | 计量不能是布尔值 | 1 |
| `PRICE_PER_INVALID` | per 必须为正整数 | 2 |
| `PRICE_UNSPECIFIED` | 试用结束，需明确免费或收费条件 | 1 |

## PRODUCT

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `PRODUCT_CONTROL_CREDENTIAL_MISSING` | 节点未保存本机控制凭据，请重启节点 | 1 |
| `PRODUCT_COORD_SELFTEST_FAILED` | 本机公共协调自检失败：{exc} | 1 |
| `PRODUCT_DAEMON_NEEDS_FIXED_PORT` | 后台启动必须指定固定端口 | 1 |
| `PRODUCT_ENCRYPTED_STORAGE_UNHEALTHY` | 加密存储读写检查失败 | 1 |
| `PRODUCT_HTTP_ENTRY_UNAVAILABLE` | 本机 HTTP 入口未就绪 | 1 |
| `PRODUCT_IMAGE_BACKEND_UNAVAILABLE` | 图像预览执行后端未就绪 | 1 |
| `PRODUCT_NODE_NOT_READY` | 节点尚未就绪，查看日志：{log} | 1 |
| `PRODUCT_NODE_START_FAILED` | 节点启动失败，查看日志：{log} | 1 |
| `PRODUCT_NODE_UNREACHABLE` | 指定节点尚未就绪，或端口与节点目录不匹配 | 1 |
| `PRODUCT_WIRING_INCOMPLETE` | 产品模块装配不完整 | 1 |

## PROJECTION

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `PROJECTION_CARD_NAME_MISSING` | 原始 Agent Card 缺少 name | 1 |
| `PROJECTION_CARD_SKILLS_MISSING` | 原始 Agent Card 缺少 skills | 1 |
| `PROJECTION_CYCLE` | 检测到投影环：{node_did} 已经做过 {role} 投影 | 1 |
| `PROJECTION_NOT_FOUND` | 投影不存在 | 4 |
| `PROJECTION_NOT_IMPORTED` | 请先导入这位 Agent | 1 |
| `PROJECTION_SIGNER_BAD_CARD` | 签名器必须返回 Agent Card | 1 |
| `PROJECTION_SIGNER_NO_SIGNATURE` | 签名器没有返回节点签名 | 1 |
| `PROJECTION_TOO_DEEP` | 投影链超过 {MAX_PROJECTION_HOPS} 跳 | 1 |
| `PROJECTION_URL_INVALID` | 投影 URL 必须是 http:// 或 https:// | 1 |

## PROTECTOR

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `PROTECTOR_REQUIRED` | 此系统需要配置 Protector；Windows 默认使用系统账户加密 | 1 |

## PROTOCOL

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `PROTOCOL_INVALID` | protocol 只能是 a2a、json 或 mcp | 2 |

## PUBLIC

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `PUBLIC_DIRECTORY_NOT_CONFIGURED` | 本节点没有配置公共目录客户端 | 2 |
| `PUBLIC_DIRECTORY_RESPONSE_TOO_LARGE` | 公共目录响应过大 | 2 |
| `PUBLIC_ENTRYPOINT_MISSING` | 本节点尚未配置可回连的公共 HTTP 入口 | 2 |
| `PUBLIC_NODE_HTTPS_REQUIRED` | 非本机公共节点必须使用 HTTPS；可信局域网需明确配置 CIDR | 1 |
| `PUBLIC_NODE_HTTP_ERROR` | 公共节点返回 HTTP {exc.code} | 1 |
| `PUBLIC_NODE_LIMIT_EXCEEDED` | 最多配置 {MAX_PUBLIC_NODES} 个公共节点 | 2 |
| `PUBLIC_NODE_PATH_FORBIDDEN` | 公共节点地址不能包含路径 | 1 |
| `PUBLIC_NODE_PORT_INVALID` | 公共节点地址端口无效：{base} | 1 |
| `PUBLIC_NODE_RESPONSE_INVALID` | 公共目录响应结构无效 | 1 |
| `PUBLIC_NODE_SKILL_INVALID` | 能力标识必须为 1–96 个字符 | 1 |
| `PUBLIC_NODE_URL_INVALID` | 公共节点地址必须是纯 HTTP(S) 基础 URL：{base} | 1 |
| `PUBLIC_SERVICES_DISABLED` | 本节点没有开放公共服务 | 1 |
| `PUBLIC_SERVICE_MANDATORY` | 在线节点的基础发现和免费交付样品不能关闭 | 1 |
| `PUBLIC_SERVICE_UNKNOWN` | 未知公共服务 | 1 |
| `PUBLIC_SUPPLY_NOT_FOUND` | 本节点没有这份公开供给或历史样品 | 1 |
| `PUBLIC_WITNESS_DISABLED` | 本节点未启用公共见证 | 1 |

## RATE

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `RATE_LIMITED` | 协调请求频率超限 | 1 |

## RELAY

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `RELAY_ABSENT` | 没有公共中继 | 1 |
| `RELAY_AUTH_MISMATCH` | 中继认证与请求内容不匹配 | 1 |
| `RELAY_AUTH_PURPOSE_INVALID` | 中继认证用途无效 | 1 |
| `RELAY_AUTH_STRUCTURE_INVALID` | 中继认证结构无效 | 1 |
| `RELAY_CAPACITY_FULL` | 公共中继卡片容量已满 | 1 |
| `RELAY_CARD_INVALID` | 中继卡片无效 | 1 |
| `RELAY_CARD_LIST_INVALID` | 中继卡片列表无效 | 1 |
| `RELAY_CARD_NOT_FOUND` | 中继卡片不存在 | 1 |
| `RELAY_CARD_NOT_VALID_SUPPLY` | 中继 Card 不是此节点对这条路径的有效供给：{why} | 1 |
| `RELAY_CIPHERTEXT_INVALID` | 中继密文编码无效 | 1 |
| `RELAY_ENTRYPOINT_MISSING` | 公共节点未配置可回连入口 | 1 |
| `RELAY_ENVELOPE_TOO_LARGE` | 中继封套过大 | 1 |
| `RELAY_LEASE_MISSING` | 供给方没有在线中继租约 | 1 |
| `RELAY_MESSAGE_TOO_LARGE` | 中继请求或应答过大 | 1 |
| `RELAY_NODE_SIGNATURE_INVALID` | 中继节点签名无效 | 1 |
| `RELAY_PROVIDER_TIMEOUT` | 供给节点未在中继期限内回复；交付状态未知 | 1 |
| `RELAY_PUBLIC_RESPONSE_INVALID` | 公共中继返回结构无效 | 1 |
| `RELAY_QUEUE_FULL` | 中继任务队列已满 | 1 |
| `RELAY_REPLY_ENVELOPE_INVALID` | 中继应答封套无效 | 2 |
| `RELAY_REPLY_NONCE_INVALID` | 中继应答 nonce 无效 | 1 |
| `RELAY_REPLY_NOT_OBJECT` | 中继应答必须是对象 | 1 |
| `RELAY_REPLY_TOO_LARGE` | 中继应答过大 | 1 |
| `RELAY_REQUEST_ENVELOPE_INVALID` | 中继请求封套无效 | 1 |
| `RELAY_REQUEST_NONCE_INVALID` | 中继 nonce 无效 | 1 |
| `RELAY_REQUEST_NOT_OBJECT` | 中继请求必须是对象 | 1 |
| `RELAY_REQUEST_TOO_LARGE` | 中继请求过大 | 1 |
| `RELAY_ROUTE_NOT_FOUND` | 中继路径不存在 | 1 |
| `RELAY_SEED_INVALID` | 中继加密种子必须是 32 字节 | 1 |
| `RELAY_SUPPLY_ID_DUPLICATE` | 中继供给标识重复 | 1 |
| `RELAY_TASK_ALREADY_DONE` | 中继任务已经完成 | 1 |
| `RELAY_TASK_FOREIGN` | 不能代领别人的中继任务 | 1 |
| `RELAY_TASK_INVALID` | 中继任务无效 | 1 |
| `RELAY_TASK_NOT_FOUND` | 中继任务不存在或不属于此节点 | 1 |

## REMOTE

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `REMOTE_TASK_ID_MISSING` | 这条记录没有远端 A2A task id | 1 |

## REQUEST

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `REQUEST_TOO_LARGE` | 协调请求过大 | 2 |

## RESPONSE

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `RESPONSE_TOO_LARGE` | 单条线索超过响应额度 | 1 |

## RESTORE

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `RESTORE_PASSPHRASE_INVALID` | 恢复口令须为 12–1024 个字符 | 1 |
| `RESTORE_PASSPHRASE_WRONG` | 恢复口令不正确或备份已损坏 | 1 |
| `RESTORE_PASSWORD_FILE_LENGTH` | 恢复口令文件长度不正确 | 1 |
| `RESTORE_PASSWORD_FILE_REQUIRED` | 请双击 A2N.exe 使用恢复页面，或指定 --password-file | 1 |
| `RESTORE_REPLACES_IDENTITY` | 恢复会替换本机身份；需明确指定 --replace-identity | 1 |
| `RESTORE_WAL_REMAINS` | 停机后仍有数据库日志文件；保留原目录，请先检查其他实例 | 1 |

## REWORK

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `REWORK_INPUT_CHANGED` | 新条件须另行协商 | 1 |

## SEED

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `SEED_LIMIT_EXCEEDED` | 最多保存 {MAX_SAVED_SEEDS} 条种子 | 1 |

## SERVICE

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `SERVICE_FLAG_NOT_BOOLEAN` | 服务开关必须为布尔值 | 1 |
| `SERVICE_ID_REQUIRED` | service_id 不能为空 | 2 |
| `SERVICE_ID_TAKEN` | 这个标识已经用于本机供给 | 2 |
| `SERVICE_UNAVAILABLE_CROSS_EXPERIENCE` | 没有跨节点体验服务 | 1 |
| `SERVICE_UNAVAILABLE_PAYMENT` | 没有支付意图服务 | 1 |
| `SERVICE_UNAVAILABLE_PUBLIC_EXPERIENCE` | 没有公开体验服务 | 1 |
| `SERVICE_UNAVAILABLE_REPUTATION` | 没有本地信誉服务 | 1 |
| `SERVICE_UNAVAILABLE_STOP` | 节点未提供停止入口 | 1 |

## SIGNED

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `SIGNED_CALLS_DISABLED` | 本节点未启用签名调用 | 1 |

## SKILL

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `SKILL_ID_INVALID` | 能力标识必须为 1–96 个字符 | 1 |

## SNAPSHOT

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `SNAPSHOT_IN_WRITE_TXN` | 不能在业务写事务中导出快照 | 1 |
| `SNAPSHOT_TARGET_MUST_BE_NEW` | 快照须使用新的目标文件与加密器 | 1 |
| `SNAPSHOT_VERIFY_FAILED` | 快照数据库校验失败 | 1 |

## SOURCE

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `SOURCE_KIND_INVALID` | source_kind 只能是 auto / local / remote | 1 |

## STORAGE

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `STORAGE_KEY_INVALID` | A2N_STORAGE_KEY 必须是 32 字节密钥的 base64 编码 | 2 |
| `STORAGE_KEY_MUST_BE_INJECTED` | 请通过容器 secret 注入 A2N_STORAGE_KEY；节点不会明文保存凭据 | 1 |
| `STORAGE_PROTECTOR_REQUIRED` | 持久化存储必须配置加密器 | 1 |

## STREAM

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `STREAM_INTERRUPTED` | 流式调用中断，请核对原任务 | 1 |

## SUPPLY

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `SUPPLY_CHARITY_TOGGLE_FAILED` | [{tag}] 打开公益开关失败：{status} {out} | 1 |
| `SUPPLY_MOUNT_FAILED` | [{tag}] 挂载 {name} 失败：{status} {out} | 1 |
| `SUPPLY_NOT_FOUND` | 供给不存在 | 1 |
| `SUPPLY_PAUSED` | 这份供给已暂停；请先恢复接单再上架 | 2 |
| `SUPPLY_UPDATE_UPSTREAM_FAILED` | [{tag}] 更新 {name} 上游失败：{status} {out} | 1 |

## TARGET

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `TARGET_REF_UNRESOLVED` | 无法从卡片确定网络 Agent 引用，请显式给 target_ref | 1 |

## TASK

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `TASK_ID_REUSED` | 同一任务标识已用于不同的请求；请使用新的任务标识 | 1 |
| `TASK_ID_TOO_LONG` | 任务标识过长 | 1 |

## TRADE

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `TRADE_FACTS_INCOMPLETE` | 交易身份必须绑定买方、卖方、商品及任务 | 1 |
| `TRADE_NOT_FOUND` | 交易不存在 | 1 |
| `TRADE_SERVICE_ABSENT` | 没有交易服务 | 1 |

## TRADES

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `TRADES_SERVICE_ABSENT` | 没有交易事实服务 | 1 |

## TRANSPORT

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `TRANSPORT_BAD_RESPONSE` | 任务控制传输层没有返回 CallResponse | 1 |
| `TRANSPORT_NO_REMOTE_CONTROL` | 当前网络传输层不支持远端任务控制 | 1 |

## TRIAL

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `TRIAL_OUTCOME_INVALID` | outcome 必须是 CallOutcome 或其 dict | 1 |
| `TRIAL_RECORD_FIELDS_REQUIRED` | 记录交付必须给出 service_id 与 task_id | 1 |

## UNKNOWN

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `UNKNOWN_AGGREGATE_STRATEGY` | 未知聚合策略：{strategy}（已注册：{sorted(STRATEGIES)}） | 1 |

## UNREACHABLE

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `UNREACHABLE` | 没有已认证的目标邻居 | 1 |

## UPSTREAM

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `UPSTREAM_NO_REMOTE_CONTROL` | 这份供给的上游不支持远端任务控制 | 1 |
| `UPSTREAM_URL_INVALID` | 上游地址必须是 http:// 或 https:// | 1 |

## WITNESS

| 码 | 说明 | 次数 |
| --- | --- | --- |
| `WITNESS_ABSENT` | 没有公共见证 | 1 |
| `WITNESS_ALREADY_WITNESSED` | 这份收据已有不同双方签名的见证 | 1 |
| `WITNESS_CAPACITY_FULL` | 本节点公共见证容量已满 | 1 |
| `WITNESS_MATERIAL_TOO_LARGE` | 见证材料过大 | 1 |
| `WITNESS_NOT_FOUND` | 没有这份见证 | 1 |
| `WITNESS_RATE_LIMITED` | 本节点公共见证当前请求过多 | 2 |
| `WITNESS_RECEIPT_HASH_INVALID` | 收据哈希不合法 | 2 |
| `WITNESS_SIGNATURES_REQUIRED` | 公共见证需要双方对同一收据哈希的有效签名 | 1 |
| `WITNESS_SIGNATURE_INVALID` | 双方哈希见证签名无效 | 1 |
