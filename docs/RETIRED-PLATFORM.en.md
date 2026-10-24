<!-- source: edc169e1fc81 -->
# Retired legacy business system inventory

Unified assembly: `Daemon → NodeRuntime → LocalStore(runtime.db)`. The old platform, old task pool, reverse-tunnel registration, central custody and demo assembly have left the product. Old databases and running data volumes are not deleted.

Already migrated: card construction and validation, exact pricing and media dimensions, acceptance policy and templates. The node's existing idempotent tasks, dual-signed receipts, encrypted credentials, trial samples, bilateral feedback and dispute records are the only implementation.

Tests for the old platform database and the financial demo retired together with the corresponding products; they must not be counted as test coverage of the new node. Pure rule tests remain in `test_migrated_rules.py` and `test_node_identity.py`, and node flows are covered by the Docker multi-node acceptance and the current tests.

Deleted paths:

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

## Old entry points and tools cleaned up afterwards

The following scripts only served the old platform, the old demos or the old package-splitting workflow, and retired with the unification migration:

- `scripts/sim_start.sh`, `scripts/sim_stop.py`, `scripts/run_node.py`
- `scripts/probe_tunnel.py`, `scripts/check_evidence_live.py`
- `scripts/ui_check.js`, `scripts/console_polish_check.js`, `scripts/console_shots.js`, `scripts/console_logic_check.js`
- `tools/split_packages.py`

On 2026-10-05 the old containers `a2n-dir-video`, `a2n-dir-finance` and `a2n-dir-play` were stopped, without deleting their containers or data volumes. The current acceptance node runs the unified image; see `artifacts/verification.md`.
