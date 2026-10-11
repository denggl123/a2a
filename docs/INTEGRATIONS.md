# 已有 Agent 接入与标准适配

所有适配都转成同一 `AgentBinding` 和本节点签名公共 Card，调用继续经过现有任务日志、免费样品、验收、支付协调、评价与争议。适配器只负责传输和结果转换，没有第二套市场业务。

## 支持范围

| 上游 | 本版本支持 | 边界 |
|---|---|---|
| A2A JSON-RPC | Card 接入、`message/send`、任务状态及取消 | 当前实现的协议子集；不宣称覆盖所有 A2A 最新版或扩展 |
| JSON HTTP | 请求/响应代理、加密凭据引用、输入声明 | 上游需符合已有 JSON 调用约定 |
| MCP Streamable HTTP | 初始化、版本协商、JSON/SSE 响应、会话头、分页 `tools/list`、`tools/call`、结构化结果 | 2025-11-25 / 2025-06-18 / 2025-03-26；不含 stdio、旧 HTTP+SSE、OAuth 自动登录、服务器反向采样及异步 MCP Tasks |

ANP、AP2、ERC-8004 等历史设想尚未实现完整适配。协议名出现于历史设计不能作为兼容承诺。x402 是独立支付子模块，不能等同于 Agent 调用协议。

## MCP 接入流程

1. 在「卖 Agent → 接入已有 MCP 工具」填写 Streamable HTTP 地址。带认证的服务选择已存在的本机加密账户，凭据不放进公共 Card。
2. 「读取可接入工具」调用初始化和 `tools/list`，不会执行任何工具。
3. 选择一个工具后接入。工具名称作为 skill id，description 和 inputSchema 保留；默认不上架，供应方确认描述、价格和公开样品规则后再上架。
4. 其他节点通过普通发现取得本节点 Card，按普通 Agent 询价和调用。第一次成功交付后的前十次样品同样强制公开；上游本身收费由供应方承担并决定是否提供。

工具只允许调用当前供给 Card 声明的名称，不能通过选择一个廉价工具调用同服务器上的其他工具。取消/重试不保证 MCP 工具撤销效果。请求丢失后保持未知，不自动重发 `tools/call`；已有 A2N 任务标识防止再执行。404 会话丢失只为后续的新调用重建会话，不重放本次未知任务。

## 接口

`POST /v1/integrations/mcp/preview` 接收 `{endpoint, account_ref?}`，返回协议版本、工具和可挂载 Card，`executed_tools=0`。通过现有 `POST /v1/bindings/http` 提交：

```json
{"protocol":"mcp","endpoint":"http://127.0.0.1:9000/mcp","service_id":"my-tool","card":{"name":"计算","version":"1","skills":[{"id":"add","name":"add","inputSchema":{"type":"object"}}]},"listed":false}
```

真实 Card 应使用 preview 返回的完整条目。重启恢复沿用原账户引用和协议。绑定上游地址仅存本机；公开投影 URL 使用本节点入口。HTTP 重定向被拒绝，认证不随重定向转发；响应、分页和时间有界。

标准依据：[MCP HTTP 传输](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)、[MCP 工具](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)、[A2A 项目](https://github.com/a2aproject/A2A)。实现范围以本页与本版本端到端验收为准。
