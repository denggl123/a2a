# 业务统一验收记录

日期：2026-10-05（Asia/Shanghai）。

## 已完成

- 唯一业务装配：`Daemon → NodeRuntime → LocalStore(runtime.db)`。
- 代码包由 23 个收敛为 5 个：kernel、p2p、acceptance、sdk、node。旧平台、中央注册、旧业务隧道和 SovereignNode 演示装配退役；卡片、计价、模板验收等纯规则迁入当前体系。
- 每个在线节点承担有界的公共发现和邻居引荐义务；上架、暂停接单、下架和可选服务设置不关闭基础发现。

## 实际执行结果

| 验证 | 结果 | 执行入口 |
|---|---|---|
| 当前全部源码测试 | 384 passed，144.70 秒 | `.venv/Scripts/python.exe -m pytest tests -ra` |
| Docker 三节点业务验收 | 74 项检查全部通过 | `.venv/Scripts/python.exe scripts/docker_acceptance.py --no-build` |
| 完整安装 | 成功，仅安装 5 个 A2N 包 | `.venv/Scripts/python.exe scripts/bootstrap.py` |
| 编译检查 | 通过 | `python -m compileall -q packages scripts` |
| 当前控制台 JavaScript 语法 | 通过 | `node scripts/console_js_check.js` |

Docker 验收使用同一节点镜像、三个独立身份和持久化卷。测试 Agent 是实际 HTTP 服务，通过受保护的管理接口上架；产品镜像没有预装演示目录。逐级发现、真实调用、幂等、验收失败、前十次样品脱敏、双向反馈验签、回执、争议撤回、故障处理、下架权限及重启恢复均已验证。逐项结果见 [Docker 验收报告](docker-acceptance.md)，原始结构化记录见 [JSON](docker-acceptance.json)。

## 当前容器状态

- `a2n-acceptance-node-a-1`、`a2n-acceptance-node-b-1`、`a2n-acceptance-node-c-1` 和实际测试上游 `a2n-acceptance-agents-1` 保持运行。
- 旧容器 `a2n-dir-video`、`a2n-dir-finance`、`a2n-dir-play` 已停止，原数据卷保留。历史中央数据库和其他数据卷未删除。

## 尚未完成的能力

- 真实公网 NAT、跨运营商连接和长期容量验证。Docker 桥接网络通过不能替代这些验证。
- 真实支付驱动；当前明确返回 `NOT_CONFIGURED`，不会把标价或历史余额当作已收款。
- 最终信誉排序、防刷与自动淘汰算法。已完成双向签名反馈、版本链和质量事实的记录与传送。
- 独立离线 EXE/MSI 安装包；当前一键入口使用 Python 环境安装完整产品。
