# 旧业务体系退役清单

统一装配：`Daemon → NodeRuntime → LocalStore(runtime.db)`。旧平台、旧任务池、反向隧道注册、集中托管及演示装配已退出产品。旧数据库和运行中的数据卷不删除。

已迁移：卡片构造和校验、精确计价与媒介维度、验收策略和模板。节点已有的幂等任务、双签收据、加密凭据、试用样品、双边反馈、争议记录为唯一实现。

原平台数据库和财务演示的测试随对应产品退役；不能算作新节点的测试覆盖。纯规则测试保留到 `test_migrated_rules.py` 和 `test_node_identity.py`，节点流程以 Docker 多节点验收及当前测试为准。

删除路径：

- `packages/a2n-account`
- `packages/a2n-ap2`
- `packages/a2n-consensus`
- `packages/a2n-custodian`
- `packages/a2n-deal`
- `packages/a2n-dispatch`
- `packages/a2n-gateway`
- `packages/a2n-ledger`
- `packages/a2n-market`
- `packages/a2n-notary`
- `packages/a2n-registry`
- `packages/a2n-reputation`
- `packages/a2n-server`
- `packages/a2n-settlement`
- `packages/a2n-store`
- `packages/a2n-task`
- `packages/a2n-transport`
- `packages/a2n-wallet`
- `packages/a2n-node/src/a2n_node/node.py`
- `packages/a2n-node/src/a2n_node/sdk_adapter.py`
- `packages/a2n-acceptance/src/a2n_acceptance/dispute.py`
- `packages/a2n-sdk/src/a2n_sdk/platform_runtime.py`
- `packages/a2n-sdk/src/a2n_sdk/tunnel.py`
- `packages/a2n-sdk/src/a2n_sdk/runner.py`
- `packages/a2n-sdk/src/a2n_sdk/shelf.py`
- `packages/a2n-sdk/src/a2n_sdk/console.py`
- `packages/a2n-sdk/src/a2n_sdk/greenlight.py`
- `tests/test_a2a_state.py`
- `tests/test_ap2.py`
- `tests/test_arbitration.py`
- `tests/test_binding_guides.py`
- `tests/test_call_gate.py`
- `tests/test_card_gate.py`
- `tests/test_client_reuse.py`
- `tests/test_connection.py`
- `tests/test_consensus.py`
- `tests/test_consistency.py`
- `tests/test_consistency_convergence.py`
- `tests/test_console_ia.py`
- `tests/test_console_local_node.py`
- `tests/test_deal.py`
- `tests/test_events.py`
- `tests/test_flow.py`
- `tests/test_free_demo_agents.py`
- `tests/test_greenlight.py`
- `tests/test_invariants.py`
- `tests/test_invoke_route.py`
- `tests/test_market_demo_agents.py`
- `tests/test_metering_wiring.py`
- `tests/test_multicurrency.py`
- `tests/test_network_ping.py`
- `tests/test_public_good.py`
- `tests/test_quality.py`
- `tests/test_refactor2.py`
- `tests/test_relay_billing.py`
- `tests/test_relay_closure.py`
- `tests/test_sdk.py`
- `tests/test_seats.py`
- `tests/test_settlement_closing.py`
- `tests/test_shelf.py`
- `tests/test_sovereign.py`
- `tests/test_standard.py`
- `tests/test_supplier_anonymity.py`
- `tests/test_supply_mount.py`
- `tests/test_transport.py`
- `tests/test_uid.py`
- `tests/test_ux_hardening.py`
- `tests/test_wiring.py`
- `scripts/sovereign_node.py`
- `scripts/sovereign_demo.py`
- `scripts/docker_demo.py`
- `scripts/a2a_smoke.py`
- `scripts/run_a2a_node.py`
- `scripts/seed.py`
- `scripts/seed_established.py`
- `scripts/run_market_demo_agents.py`
- `scripts/run_free_demo_agents.py`
- `docker/agent_node.py`
- `docker/market_node.py`

## 后续清理的旧入口与工具

以下脚本只服务于旧平台、旧演示或旧包拆分流程，已随统一迁移退役：

- `scripts/sim_start.sh`、`scripts/sim_stop.py`、`scripts/run_node.py`
- `scripts/probe_tunnel.py`、`scripts/check_evidence_live.py`
- `scripts/ui_check.js`、`scripts/console_polish_check.js`、`scripts/console_shots.js`、`scripts/console_logic_check.js`
- `tools/split_packages.py`

2026-10-05 已停止旧容器 `a2n-dir-video`、`a2n-dir-finance`、`a2n-dir-play`，未删除其容器和数据卷。当前验收节点运行统一镜像，详见 `artifacts/verification.md`。
