<!-- source: f548569c2e45 -->
# Current script entry points

Scripts are operations and acceptance tools; the product's only business entry point is still the node's `TradeService` and the full SDK. Start with [current status](../docs/PROJECT-STATUS.en.md). On Windows use the repository's `.venv\Scripts\python.exe`; on a server use the Python environment where the SDK is installed.

## Everyday entry points

| Purpose | Entry point | Modifies existing nodes |
|---|---|---|
| Full source install, self-check and start | root `install.bat` / `install.sh`; `bootstrap.py` | Installs and starts explicitly |
| Installed product self-check, query, stop | `a2n-sdk doctor / request / stop` | Query is read-only; stopping takes an explicit action |
| Source, documentation and bundle inspection | `project_audit.py` | Read-only; writes a public summary report |
| Current four-node state and live code inspection | `project_audit.py --installed` | Read-only; calls no Agent and pays nothing |
| Current rules, version and release check | `release_check.py`; for a release bundle add `--artifacts --desktop --doctor --require-signed` | The self-check runs isolated and changes no business node |
| Source bundle / full Windows installer | `build_sdk_bundle.py` / `build_desktop_bundle.py` | Updates build artefacts; the latter signs with the local project publisher by default |
| Local network configuration | `start_network.ps1` / root `start-network.bat` | Uses the existing identity and volume; an explicit network deployment |
| Legacy scheduled-task start, read-only guard check | `a2n_job_wrapper.py --home <dir>` / `a2n_job_wrapper_livecheck.py --home <dir>` | Starting resolves the installed version dynamically; the check neither starts nor terminates a node |
| Local payment test snapshot | `anvil_snapshot.py` / `facilitator_snapshot.py`, optionally `--backup-root` and `--keep` | Reads existing state and writes a verified backup; does not restore, restart or create scheduled tasks |
| Upgrade the four nodes keeping data | `deploy_local_completion.py --report-prefix <label for this run>` | Backs up, upgrades and rebuilds explicitly; recovery secrets stay encrypted on this machine only |

## Current acceptance entry points

| Scenario | Entry point | Scope |
|---|---|---|
| Automated regression and module boundaries | `python -m pytest tests`, `node runtime_logic_check.js` | Temporary data; touches no existing node |
| Real business through an isolated browser | `console_acceptance.py` | A temporary node; needs Playwright and a browser |
| Real business across three Docker nodes | `docker_acceptance.py` | A dedicated acceptance environment; once built it can serve as the local node network |
| Discovery and scoring across four isolated nodes | `selection_acceptance.py --image a2n-node-test` | Creates containers dedicated to this run and picks the LAN address from current configuration; `--host` overrides |
| Real trade across the installed four nodes | `automatic_settlement_acceptance.py` | Lists a controlled text Agent explicitly: first ten samples, points / native coin / x402 and original-order recovery; the test offering is delisted once policy is restored |
| Feedback, reputation and negotiation across the installed four nodes | `local_business_acceptance.py` | Controlled calls and feedback; keeps real facts but does not stand in for human calibration data |
| Workflows, complex deliverables, independent review | `vision_completion_acceptance.py` | Installs real tools and creates a fresh run_id and an independent report; `--resume <report path>` only resumes the original run |
| Restart consistency on the local test chain | `payment_testchain_check.py` | Uses local Anvil explicitly, with no real funds; checks the original transaction, balances and replay |

Acceptance against installed nodes produces real samples and ledger entries, so it has to be run explicitly. Private labels, engineering labels and human labels must not be mixed. A passing inspection is neither proof that these business acceptances ran nor a public-network acceptance.

## Historical and specialised tools

`completion_*`, `vision_*_snapshot.py`, `r1_cross_node_check.py`, `deploy_local_points.py` and several per-domain browser checks keep the review and migration capability of their own stage. The scripts that read `*-2026-10-09.json` in particular cover that one historical run and cannot replace a new acceptance; do not chain them into a new default deployment flow. `repair_local_test_nonces.py` applies only to the documented test-chain migration, not to an ordinary restart or a real network.

`free_demo_check.py` was removed because it depended on the retired platform and the old SDK API; the early `p2p_demo.py` demo entry point has been replaced by the real coordination layer and node acceptance. Existing API-compatible entry points route to the same current implementation; there is no second business implementation to maintain.
