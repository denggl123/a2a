# 文本结构分析 Agent

实际处理输入文本，返回字数、行数、段落数、词频、短摘要和输入摘要。英文按单词、中文按单字统计；不使用外部模型或 API。

运行：`python examples/text_inspector/agent.py --port 9000`。

以 HTTP JSON 协议挂载 `/invoke`，技能为 `inspect`。输入示例：`{"text":"Hello agent.\n\nHello market."}`。`/counts` 返回实际请求计数，供业务验收检查重复请求是否重复执行。

`scripts/local_business_acceptance.py` 会显式启动该服务，通过四节点的管理接口上架和调用。普通 SDK 启动不自动加载该供给。

初始十次免费技术交付按市场规则公开为脱敏样品。请为这些调用使用适合公开采样的文本。

为了让控制台按字段填写，可以在技能声明中添加：

```json
{"id":"inspect","name":"分析文本","input_schema":{"type":"object","properties":{"text":{"type":"string","title":"要分析的文字","minLength":1,"maxLength":20000}},"required":["text"]}}
```

目前表单支持最多 32 个字符串、整数、数字、布尔或枚举字段，以及必填、范围、长度和默认值；嵌套、组合规则和引用自动使用 JSON 编辑器。浏览器无法精确表示的大整数应与服务约定用字符串传递。修改输入后必须重新询价，已经接受的订单仍保留原输入。
