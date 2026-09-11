# A2N 架构评估 · 第四轮（收口：从"能跑对"到"崩溃也不说谎"）

评估方式：以第三轮待修清单（A–L）为底逐条核对 + **模拟运转实测暴露**（31 项冒烟 + 二轮）
+ 代码逐条复核后决定修与不修。**本轮以"表归属 / 事件事务 / 记账闭环"三条线为纲**，
修完必须能在活库上拿出证据（本报告所有数字均来自 data/e2e_a2a.db 实测）。

> 重要背景：本轮大部分修复不是"设计出来的债"，而是**跑出来的债**——
> 冒烟与二轮模拟暴露的问题（中继不记账、观测被心跳覆盖、自报 TTFT 恒空）
> 恰恰都是"测试全绿但模拟运转才现形"的类型。测试 252 项全绿只是底线。

## 总体判断

| 维度 | 三轮 | 本轮 | 说明 |
|------|------|------|------|
| 表归属与数据一致性 | 8.5 | **9.5** | 每张表单一 owner；四表贯通；事件与业务同事务、通知延迟到提交后 |
| 治理链完整性（调用必经） | 8 | **9.5** | 中继调用记账闭环（收费=对等成交 / 免费=零账），"有凭据就能不记账"已堵死 |
| 资金安全与合规 | 8.5 | **9** | 多币种分账隔离；剩余为审计级硬化（SSRF / 限速） |
| 可观测与文档 | 8 | **9** | 延时四口径在**唯一实际链路上**全部有数据；CALL-PARAMS 对齐黑盒口径 |
| SDK 工程质量 | 7 | **9** | 签名守门、失败如实上报、转发执行纳入本机观测 |
| **综合** | **8.5/10** | **9/10** | 三条线闭环；剩余均为"可排队的工程债"，不影响正确性 |

## P0 · 已修（本轮）

| # | 问题 | 修法 | 活库证据 |
|---|------|------|---------|
| 1 | **中继调用不过账**：凭据一到手，收费 agent 被白调，供给方白干 | `_bill_relay_call`：收费+ACTIVE 配对 → 按对等成交进 deals（task_id=None）；免费零账；失败不记；结果走 `X-A2N-Billing` 响应**头**，A2A body 保持纯净 | 二轮 6 次中继：bob 3 笔 deal、carol 0 笔；deals=4/charges=2/免费 0 账 |
| 2 | **SDK 容器节点 handler 签名错**（`fn(path,payload)` 挂成任务处理器）→ 静默 TypeError，任务永卡 ASSIGNED | `handle`/`handle_http` 拆分；启动时 inspect 签名，错了**启动即报** | 冒烟全链通过，无卡单 |

## P1 · 已修

| # | 问题 | 修法 |
|---|------|------|
| 3 | 对等额度 `exposure_fen` 读-判-写竞态（可超敞口） | `record_peer` 整体包 `tx()`，deal 三动作 `commit=False` 交接 |
| 4 | `publish` 在 `commit` 之后（a2n-task / dispute.resolve）→ 崩在中间则事件与事实分离 | 先 publish 后 commit（事件进同事务 outbox） |
| 5 | **订阅者嵌在发布者事务里 commit**：事件一到就写自己的库，把发布者事务提前提交 | `store.on_commit/on_rollback` 钩子 + events 延迟通知队列：事务内只排队，提交后才投递；回滚即丢弃（tests/test_events.py 5 项） |
| 6 | 路由层直写 `a2a_tasks`（A）与 `rosters`（B）——表归属破坏 | `a2n-task.save_view()` 成为 a2a_tasks owner（状态不复制，读时由 tasks 计算）；新建 `a2n-registry.rosters` 模块收编读写与 p2p 合并 |
| 7 | 多币种串味：custodian_book 无币种列，`balance_fen` 把 USDC 当"分"累加 | 加 `currency` 列；`balance_fen` 只锚 CNY，新增 `balance_of(cur)`；对账文档明确只锚 CNY |
| 8 | 声誉事件永不生效：`node.suspended` / `arbitration.lost` 无人订阅 | wiring 接上两个事件 |
| 9 | 全局异常裸奔：A2NError 落 500、裸 ValueError 泄内部 | `@app.exception_handler`：A2NError→自带 http_status；兜底 500 脱敏 |
| 10 | 终态口径混淆："完成"把 REJECTED 也算了；GMV 混入非积分 | 完成 = SETTLED+ACCEPTED；GMV/积分只计 SETTLED；console 同步措辞 |

## 本轮冒烟实测暴露并修复（测试绿但模拟才现形）

| # | 问题 | 根因 | 修法 | 活库证据 |
|---|------|------|------|---------|
| 11 | **使用端观测被心跳覆盖**：6 条观测回传 200，库里只剩 3 条 | `heartbeat` 读 connection 在 `tx()` **外**——`BEGIN IMMEDIATE` 等锁期间 observe 提交，心跳用陈旧副本写回。等待窗口=对方事务全程，不是微秒 | 行读取移进锁内（`observe` 本就在锁内，两写者从此真正互斥） | 修复前 6 丢 3；修复后 3+3 全存活（含确定性回归测试：暂时性把 tx 推迟到并发写者之后，旧代码必挂） |
| 12 | **自报 TTFT 恒空**：设计在、参数在、样本永远 0 | v1 主链路是编排层**通道转发 + 平台代提交**（delivery=inline），任务推送通道基本不触发；SDK 只在推送通道记账 | `TunnelClient.on_execute` 回调：转发执行（成功/失败、耗时、技能）纳入本机观测窗口（TTFT=结果就绪时刻，与推送通道非流式口径一致） | bob 5 样本 304ms / carol 4 样本 301ms / erin 1 样本 305ms（修复前全 0） |

## SDK / 控制台 / 文档（同轮收口）

| # | 项目 | 说明 |
|---|------|------|
| 13 | `call_agent` 401 自动重取一次凭据 | 凭据过期不再打断使用方 |
| 14 | SDK 补齐 `fail()/pay_channels()/pay_methods()/bind_pay_method()/close_pay_method()/pay_compatible()/charge()` | 节点侧不再需要手写 HTTP |
| 15 | `gpu_seconds` 从 SDK 计量移除 | 有能力黑盒口径：SDK 无从知道真用了 GPU，硬报是口径污染 |
| 16 | `connection_report` 不再自判 direct | 有 url ≠ direct（可能是内网/占位地址）；改成留空由平台按公网性与来源 IP 判定，显式声明仍是节点权利 |
| 17 | console：`esc()` 转义引号（XSS）/ `curMoney` 多币种格式化 / `apiAll()` 单源失败不白屏 / SLA 声明列 | 四口径在发现页凑齐 |
| 18 | docs/CALL-PARAMS.md 对齐 | 删"算力"声明（黑盒化后只剩 deployment.region）；补 `X-A2N-Billing` 计费头；补 region 投影说明 |

## 验证（本报告所有数字的出处）

- **pytest 252 项全绿**（本轮净增 13 项：events 5 / relay_billing 3 / multicurrency 2 / connection 3）。
- **冷启模拟**：清库 → 平台 8000 + 三节点（9102 收费 / 9103 免费 / 9104 x402）→ 冒烟 31 项通过
  （发现→免费直调→收费门禁→直付凭证→对等账户→x402 即付）→ 二轮（黑盒投影 / region 筛选 / 使用端聚合轮询 6 连发）。
- **活库四表核验**：tasks=4（ACCEPTED×4）、deals=4、pay_charges=2（CAPTURED×2）、a2a_tasks=4（无状态列=单源）、
  免费单无 deal 无 charge、custodian_book 带币种列、事件流 12 类全生命周期。
- **自报指标与观测终态**：心跳一个周期后 bob ok=5/ttft 304ms、carol ok=4/301ms、erin ok=1/305ms；
  使用端观测 3+3 全存活（修复前 6 丢 3）。
- **无锁冲突、无 5xx**：平台日志仅 1 条预期 402（x402 挑战）。

## 剩余清单（P2，均不影响当前正确性）

| # | 问题 | 位置 | 建议 |
|---|------|------|------|
| A | `dispatch()` 对未知结算方式返回 `{kind:"none"}` 被静默接受 | `gateway/settle.py` | 未命中 SETTLERS 时按"没记账"如实标注/告警 |
| B | `record_charge/capture/fail_charge` 自带 `conn().commit()`，无 commit 交接参数 | `gateway/settle.py:135,149,160` | 当前调用链安全；未来若被包进事务需先补 `commit=False` |
| C | 观测无任务绑定时可灌水（一期接受） | `registry.observe` | 生产强制 task_id 绑定 |
| D | `probe_inbound` SSRF 硬化（DNS 重绑定） | `reachability.py` | 解析后复验 IP + 禁重定向 + 出网代理 |
| E | `verify-token` 无限速（direct 节点验票接口） | `routers/transport.py` | 加简单令牌桶 |
| F | 策略/注册表进程内全局，多 worker 会漂移 | 三处注册表 | 单进程部署可接受；多 worker 前集中落库 |
| G | 使用端观测窗口里 rtt 与 total 样本数不齐时 samples 取 max | `registry.observe` | 展示层按各自列表长度标注，或拆两个 samples |

## 结论

- **架构可以进入下一阶段**：三条线（表归属 / 事件事务 / 记账闭环）闭合后，
  "崩溃也不说谎"这条承诺有了代码级依据——钱与终态同生共死、事件与事实同生共死、
  调用与记账同生共死（免费除外，且免费是服务端判定的）。
- **本轮的教训写进流程**：测试全绿 ≠ 系统正确。第 11/12 两条都是只有"跑起来看库"才现形的问题，
  建议以后每轮收口都保留"清库冷启 + 冒烟 + 二轮 + 活库核验"这一节，作为发布前的固定动作。
- **下一轮建议**：剩余 P2 按 A→C→D 排序；其中 C（观测绑定）牵涉"使用端数据可信度"，
  与发现页排序质量直接相关，值得优先。
