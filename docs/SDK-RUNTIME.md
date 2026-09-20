# A2N 节点运行时

## 一句话

**一个节点代表一个人；一个运行时可以挂很多 Agent。** Agent 可以在本机、Docker、
局域网或远程云端。其他人只看见由这个节点签名并负责交付的投影，不接触真实地址和账户。

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

网络路径也只是端口实现。`FallbackTransport` 可以按“自持签名直连 → 标准 A2A →
平台”装配；默认只在尚未建立连接（`UNREACHABLE`）时换路，业务拒绝和超时不会自动
重试，避免同一任务产生两次副作用。NAT 打洞/QUIC 以后只需新增 `TransportPort`
适配器，不改工作台入口、调用、验收或结算层。

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

## 平台兼容桥

现有平台服务端仍按一张卡建立一条隧道。`RuntimePlatformBridge` 将这个历史约束封装在
适配器里：对用户仍然只有一个 `NodeRuntime` 和一个本机端口，后续换成 P2P/QUIC 时不改
挂载、投影和调用业务。

```python
from a2n_sdk import RuntimePlatformBridge

bridge = RuntimePlatformBridge(runtime, base_url="http://127.0.0.1:8000")
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
