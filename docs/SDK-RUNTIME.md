# A2N 节点运行时

## 一句话

**一个节点代表一个人；一个运行时可以挂很多 Agent。** Agent 可以在本机、Docker、
局域网或远程云端。其他人只看见由这个节点签名并负责交付的投影，不接触真实地址和账户。

每个 SDK 节点自己提供 `/console`、A2A 接入和凭据保险箱：装在个人电脑，这台电脑就是
服务端；装在服务器，那台服务器就是节点。控制台直接管理当前节点，不需要再“连接自己”。
平台注册表、中继、结算或其他索引都只是可选适配器；远程控制台只有在用户主动授权时才
通过一次性配对访问本节点。

## 分层

```text
AI 工作台 / 控制台
        │ A2A
localhost A2A 网关                 a2n_sdk.gateway
        │
调用流水线：传输 → 验收 → 结算     a2n_sdk.pipeline
        │              │
TransportPort          AcceptancePort / SettlementPort
        │
平台 / 直接 A2A 适配器             a2n_sdk.adapters
自持节点签名直连适配器              a2n_node.sdk_adapter

发现控制面（不承载任务载荷）
        │
DiscoveryPort                       a2n_sdk.ports
        │
局域网 beacon / 引导节点 / gossip   a2n_node.p2p_service

供给侧网络入口
        │
BindingTable → UpstreamPort         a2n_sdk.upstream
                 ├─ Python 函数
                 ├─ 本机 HTTP/A2A
                 └─ 远程 HTTP/A2A + 本地账户引用
```

模块纪律：

- 网关只翻译协议，不做网络选择、验收或结算；
- 调用流水线只依赖四个 `Protocol`，不 import 平台与 P2P 实现；
- 网络层只负责送达，不得宣布质量通过或动账；
- 供给侧只声明“已经交付”，验收属于调用方；
- 账户凭据只在本机保险箱解析，不进入 Card、发现消息或平台注册表。
- P2P 只发现并验签 Card；任务正文不走 gossip，发现也不替传输层保证地址可达。

网络路径也只是端口实现。`FallbackTransport` 可以按“自持签名直连 → 标准 A2A →
平台”装配；默认只在尚未建立连接（`UNREACHABLE`）时换路，业务拒绝和超时不会自动
重试，避免同一任务产生两次副作用。NAT 打洞/QUIC 以后只需新增 `TransportPort`
适配器，不改工作台入口、调用、验收或结算层。

## 长任务与取消语义

远程 A2A `message/send` 返回非终态 Task 时，`A2AUpstream` 会在**同一个总超时预算**内
调用 `tasks/get`，直到 `completed / failed / rejected / canceled`；`input-required` 和
`auth-required` 会立即交还工作台，不会被错误轮询到超时。轮询期间保留远端
`task id`、`context id`、状态说明和响应元数据，不会把每次查询伪装成新任务；超过期限会
返回 `TIMEOUT`，并在元数据里留下最后一次远端 Task，方便人工追查。本机把原始请求加密
持久化；之后工作台再次发标准 `tasks/get` 时，会只用远端 task id 查询一次。若远端此时
完成，结果仍回到同一套验收、结算流水线，不会绕过业务层。A2A/JSON 的 HTTP
408 或 5xx 不会被武断写成业务失败，而是返回 `DELIVERY_UNKNOWN` 并标记远端副作用未知。

本机网关同时提供 `tasks/get` 与 `tasks/cancel`。这里的“取消”有意采用保守口径：

- 尚未开始的排队任务可以在执行前取消；
- 已经进入 Python 线程或发往远端的任务无法被安全强杀，本机只记录中间态
  `CANCEL_REQUESTED`，A2A 标准状态仍诚实显示为 `working`；
- 运行中取消会明确返回 `remote_effect_unknown=true`：它表示“已经请求取消”，
  **不表示远端一定停止，也不表示副作用已经回滚**；
- 若执行、验收或结算后来仍然完成，最终真实结果会覆盖 `CANCEL_REQUESTED`，并保留
  `cancel_requested=true / cancel_acknowledged=false`，不能用取消界面隐藏真实扣款；
- 节点崩溃或重启后，遗留的运行中取消请求会收敛成终态 `INTERRUPTED`，明确保留交付结果
  未知且禁止自动重放，而不是永久卡在 `CANCEL_REQUESTED`；
- 已经拿到远端 task id 的超时/等待中任务，会把本机 `tasks/cancel` 转发成远端 A2A
  `tasks/cancel`；若使用了回退传输，只允许沿最初创建任务的精确路径转发，绝不切到另一个
  提供者误取消同名任务；
- 远端明确返回 `canceled` 才标 `cancel_acknowledged=true`。取消请求自身超时、5xx 或协议
  失败只附加在原任务记录上，不会把“取消失败”伪装成“任务失败”。

因此，只有排队阶段返回的 `canceled` 或远端明确确认的 `canceled` 才代表已取消；运行中的
未确认请求不能作为重放有副作用请求的依据。`TIMEOUT` 只终止本机等待，同一 task id 不会
重新发送 `message/send`，但只读 `tasks/get` 可以安全地继续追踪原远端任务。

本地执行线程也采用同一条诚实原则：Python 无法安全强杀已经运行的任意函数。节点停机时
先取消尚未开始的队列项，对运行项只等待有限宽限期并持久化 `INTERRUPTED`；执行线程是守护
线程，不会无限拖住进程退出，后来返回的迟到结果也不能覆盖停机时已经落盘的不确定状态。

## 本地 Agent 与远程 Agent

```python
from a2n_sdk import NodeRuntime

runtime = NodeRuntime("did:a2n:你的节点")
runtime.start_gateway(port=8771)

# 本地函数
runtime.mount_callable(
    {"name": "本机 OCR", "url": "http://127.0.0.1:9102",
     "skills": [{"id": "ocr"}]},
    lambda payload: {"text": local_ocr(payload["image"])},
    service_id="ocr",
)

# 远程 A2A；密钥只存在账户保险箱
runtime.add_account(
    "video-cloud", label="我的视频云账户",
    headers={"Authorization": "Bearer ..."},
)
runtime.mount_http(
    remote_card, "https://agent.example/a2a", protocol="a2a",
    account_ref="video-cloud", service_id="video", source_kind="remote",
)
```

两个供给共用同一个本机端口：

```text
http://127.0.0.1:8771/a2a/ocr
http://127.0.0.1:8771/a2a/video
```

## 两种投影

### 供给投影

真实 Agent Card 的地址不对外。SDK 生成一张新卡：

```text
真实 Agent Card
→ source_card_hash
→ url 改为当前节点入口
→ 加 node_did / service_id / projection_chain
→ 当前节点重新签整张投影卡
```

当前节点是交付责任人。远程上游坏了，投影节点应返回真实失败，不能把责任藏在上游地址后面。

### 使用投影

发现到网络 Agent 后，本机 SDK 再生成一张 localhost Card：

```text
网络 Agent
→ http://127.0.0.1:8771/a2a/proj_xxx
→ 复制进任何支持 A2A 的 AI 工作台
```

工作台只会调用 localhost。身份、网络路径、账户选择、验收与结算都由本机 SDK 完成。

## 本机资源生命周期

本机控制台的配置不再只有“添加”：

- 使用投影可移除；移除后 localhost Card 立即失效，同时停止后台网络观测；
- 供给挂载可暂停、恢复或卸载；暂停已发布供给时，先在本机立即停用并撤掉 P2P 广播，
  再把平台可见性改为 `private`、停止隧道并持久化为 `paused`；恢复沿用原 agent id 重新发布；
- 如供给仍在平台公开，默认拒绝卸载，必须明确选择“同时下架并卸载”；
- 平台下架先把可见性改为 `private`，成功后才停止隧道并删除本机发布记录；若远端操作
  失败，本地不会假装已经下架；
- 账户删除前检查供给挂载和使用投影的引用，仍被使用时拒绝删除；
- 显式下架会写入本次进程的墓碑，异步恢复线程不能拿旧快照把它重新上架。

这些规则由 `RuntimeManagement` 负责编排，账户保险箱、运行时资源表和平台适配器各自只
处理自己的状态，避免把外部发布生命周期塞进底层容器。

## 后台网络观测

导入网络 Agent 后，`NetworkMonitor` 会立即开始并按固定间隔做 TCP connect 探测，保存
最近 30 个样本（可达性、连接耗时、时间），供控制台画出类似 VPN 客户端的延迟走势；
也可以通过 `/v1/network/probe` 手工刷新一次。

P2P 控制面会对已握手邻居发送签名 UDP PING/PONG，保存每个邻居最近 30 个 RTT 样本，
并统计收包、发包和签名/格式异常丢弃数。探测有固定并发上限，邻居较多时轮换采样，慢节点
不会把后台线程池越堆越大；控制台可通过 `/v1/discovery/probe` 立即刷新。

这个指标只说明“从我这里到对方 HTTP 端口能否建立 TCP 连接”。它**不会调用 Agent、
不会创建任务、不会参与验收，也不会产生费用**，不能拿来冒充端到端耗时或质量分。

调用传输层另有独立的路径候选表。`FallbackTransport` 按 priority 排序 direct / relay /
未来的 QUIC 等适配器，只在确认“尚未连上”（默认 `UNREACHABLE`）时安全降级；每次调用把
候选、尝试顺序和最终路径写进任务网络元数据。任务后续查询/取消固定使用最初路径，不重新
选路。这个选择层已经可用，但它不是 NAT 打洞本身。

## P2P 发现接入

常驻节点可以把 `a2n-p2p` 作为 `DiscoveryPort` 接到同一套运行时：

```bash
a2n-node serve --home data/my-node --port 8771 \
  --bootstrap seed.example.com:9701 \
  --p2p-public-base https://my-node.example
```

- 同一局域网默认用 beacon 找邻居；跨网冷启动可重复传入 `--bootstrap HOST:PORT`。即使关闭
  LAN beacon，引导节点握手、保活和过期清理仍会独立运行；
- gossip 只按查询技能返回 MTU 内的紧凑索引（HTTP 入口、Card 哈希），不广播完整目录、
  Card 或任务载荷；握手也必须验签并满足 `DID = 公钥指纹`。QUERY 只接受已经完成签名
  握手的直邻，防止陌生 UDP 包借节点放大；同技能结果超过单包容量时会轮换批次；
- 当前只对直接邻居拉取完整 Card：连接固定到实际发来签名 OFFER 的源 IP，禁重定向并限制
  响应体大小，再核对卡片签名、所属 DID、完整卡哈希、技能和入口；任一项不一致都不导入。
  查询与并行 Card 拉取共享一个总超时预算，因此“多跳看到线索”不承诺能跨过非直邻安全边界；
- **只有显式配置 `--p2p-public-base` 才广播本机供给。** 未配置时节点仍能发现别人，
  但发布空索引，避免把 `127.0.0.1` 或未经确认的内网地址宣传成别人可调用的服务；
- `--p2p-public-base` 是操作者对“这个 HTTP 入口可被其他节点访问”的明确声明，当前
  运行时不会替它做公网可达性证明。若它由同机反向代理提供，代理需保留 `Host`，或传入
  匹配的 `X-Forwarded-Host / X-Forwarded-Proto`；精确匹配该公共入口时，节点允许获取
  哈希完全一致的投影 Card 并向其 A2A 路径发起调用，但账户、配置、任务列表等管理接口仍
  必须来自本机控制台会话或显式远程配对。能力票据/鉴权仍由业务入口负责，配置 URL 不会
  自动开放管理口。

当前接线是“LAN / 引导节点 / 签名 gossip 发现 + 对已声明 HTTP 入口的标准 A2A 调用”。
P2P 层另有经活跃邻居协调的 UDP 打洞（锥形 NAT 通常可通；对称型 NAT 打不通，跨真实
公网 NAT 尚未实测）与自愿密封中继。它**仍不包含 ICE/QUIC 完整路径协商**；两端都在
不可互访的 NAT/CGNAT 后面且打洞失败时，发现到对方也不等于能调用——应改走中继。
平台已有的 `/v1/relay` 是中心平台隧道的底层原语，
不是这条 P2P 发现链路已经拥有的去中心化中继。

同样，`SettlementPort` 只是解耦出的业务端口。平台托管结算和自持模式的免费/双边互证
可以分别装配，但**通用去中心化自动结算、多币种原子交换与跨节点争议退款仍未完成**。

## 平台兼容桥

现有平台服务端仍按一张卡建立一条隧道。`RuntimePlatformBridge` 将这个历史约束封装在
适配器里：对用户仍然只有一个 `NodeRuntime` 和一个本机端口，后续换成 P2P/QUIC 时不改
挂载、投影和调用业务。

```python
from a2n_sdk import RuntimePlatformBridge

bridge = RuntimePlatformBridge(runtime, base_url="http://127.0.0.1:18787")
bridge.publish("ocr")
bridge.publish("video")
```

## 投影签名纪律

原卡不能修改 URL 后继续携带原签名。节点 SDK 的投影是新卡，必须由投影节点重新签名，
并保留原卡哈希。平台没有供给方私钥，只能生成明确标注 `attested=false` 的兼容视图；
它不得伪装成供给方签过的卡。

正式节点用 `a2n_node.runtime_from_sovereign(node)` 装配运行时：私钥和签名实现留在
`a2n-node`，`a2n-sdk` 只接收签名回调。直接构造 `NodeRuntime` 而没有注入 signer 时，
投影会明确标注 `attested=false`，只能作为开发或兼容入口。
