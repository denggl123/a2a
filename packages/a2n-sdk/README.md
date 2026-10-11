# a2n-sdk

此 Python 包提供节点业务契约、运行时、管理客户端与纯业务模块，只依赖标准库。用户安装的「完整 SDK」是根目录发布的 A2N 产品，同时包含 a2n-node、内核、网络、验收、支付适配器和控制台。

## 安装与当前入口

完整产品安装见 [项目说明](../../README.md)；文档以 [当前入口](../../docs/README.md) 为准。唯一节点默认本机入口为 `http://127.0.0.1:8771`，控制台为 `/console`。旧中心平台及其账户、提现、AP2 接口已移除，当前程序统一连接自己的节点。

## 程序调用

```python
from a2n_sdk import NodeClient

# owner_token 是本机管理凭据；不要放进 Agent 描述、输入或公开样品。
client = NodeClient("http://127.0.0.1:8771", token=owner_token)
task = client.call("my-imported-agent", "inspect", {"text": "需要处理的文本"},
                   task_id="my-stable-request-id")
# 未定结果查询原单；同一 task_id 重试不能换输入。
latest = client.get_task("my-imported-agent", "my-stable-request-id")
```

`call` 返回 A2A Task。`call_agent` 返回交付内容，并接受相同的 `task_id / metadata / automatic` 参数。未完成时抛出包含原任务的 NodeRequestError；只有节点证明执行未到达远端时才是 SafeRetryError。底层 UNKNOWN 不能自动重新调用 Agent。

供应方配置支持的支付方式，使用方配置方式顺序、金额上限与自动授权。自动结算默认关闭；开启后普通 SDK 调用会固定报价及原计划，由后台恢复同一订单。关闭授权阻止新的付款，已经发生外部影响的原单继续核对。前十次真实免费技术交付一定成为脱敏公开样品，不创建任何结算记录。详细规则见 [自动结算](../../docs/AUTOMATIC-SETTLEMENT.md)。

## Agent 接入

完整节点支持 JSON HTTP、A2A 和 MCP Streamable HTTP，统一经过调用、样品、验收和反馈链。`NodeClient.mount` 可挂载 HTTP Agent；MCP 工具先预览再选择并授权接入。具体示例与边界见 [接入指南](../../docs/INTEGRATIONS.md)。

SDK 内的 `NodeRuntime.mount_callable` 可挂载本进程函数，但完整市场服务需使用 `a2n_node.Daemon` 进行装配。Agent Card 由本机节点签名并发布，不能把未验证的外部 Card 当成签名节点。

## 模块边界

- `ports` 定义调用、验收、结算与传输接口；`runtime` 负责装配。
- `settlement_policy` 只保存规则和纯匹配；`payment_coordination` 维护合同和统一订单。钱包、链上 RPC 和具体支付驱动位于 a2n-node。
- `selection` 读取归一化特征与私人偏好；发现只负责找节点，筛选和排序不执行付款。
- `task_quality`、校准、争议、反馈和金融记录独立持久化；技术交付不等于语义质量通过。

当前为 Beta，版本来自根目录 VERSION。许可为 [Apache-2.0](LICENSE)，发布验收见 [发布规范](../../docs/RELEASING.md)。
