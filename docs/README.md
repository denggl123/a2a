# A2N 当前文档入口

当前产品版本以根目录 [VERSION](../VERSION) 为准。项目处于 Beta；功能验收与真实独立用户校准分别记录，局部测试通过不代表全部网络环境通过。

## 使用与业务规则

- [安装与概览](../README.md)：完整 SDK 产品、唯一节点业务体系。
- [服务器安装](SERVER-DEPLOYMENT.md)、[Docker 节点](../docker/README.md)。
- [已有 Agent 接入](INTEGRATIONS.md)：A2A、JSON HTTP、MCP HTTP 的实际支持范围。
- [自动结算规则](AUTOMATIC-SETTLEMENT.md)：供应方支持列表、使用方顺序、额度与原单恢复。
- [支付协调](PAYMENT-COORDINATION.md)、[积分规则](POINTS.md)、[x402](X402.md)。
- [发现规则](COORDINATION-RULES.md)、[协调接口](COORDINATION-API.md)、[发现后筛选](DISCOVERY-FILTERS.md)。
- [双方反馈](FEEDBACK-RULES.md)、[评估模块](SELECTION-MODULE.md)、[任务质量和校准](TASK-QUALITY-CALIBRATION.md)。
- [私人八维偏好学习](PRIVATE-PREFERENCE-LEARNING.md)：只从授权真人评价生成建议，采用前检查版本与撤回。

## 架构与开发

- [当前五包架构](SDK-ARCHITECTURE.md)、[单一节点迁移](NODE-UNIFICATION.md)。
- [发布与验收](RELEASING.md)、[贡献指南](../CONTRIBUTING.md)、[安全说明](../SECURITY.md)、[变更记录](../CHANGELOG.md)。
- [愿景实现](VISION-IMPLEMENTATION.md)、[补齐工作](VISION-COMPLETION-WORK.md)、[工作流与维护](WORKFLOW-MEDIA-MAINTENANCE.md)。这些包含此前交付记录，当前版本能力以代码和本版本验收结果为准。

## 历史资料

`docs/design/`、`REVIEW-ROUND*.md`、`ARCHITECTURE-REVIEW.md`、`OPTIMIZATION-PLAN.md`、`DEVELOPMENT-ROADMAP.md`、`PRODUCT*.md`、`SDK-RUNTIME.md` 等保留原始设计和阶段评估。旧平台接口、商业化讨论、历史完成率以及当时的未完事项均不构成当前运行规范。设计稿里的 AP2、ANP、ERC-8004 等设想不等于已实现的标准适配。

冲突时依次检查：当前代码和验收结果 → 本入口链接的当前规则 → 愿景设计 → 历史资料。新增行为必须更新当前规则及对应的有意义的验收。
