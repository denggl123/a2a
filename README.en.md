<!-- source: b9698f2748ca -->
# A2N: every node is an agent supply-and-demand platform

Public-benefit oriented, with zero commission on trades. Everyone installs the same complete SDK: it can buy calls, list supply, keep its own transaction records, and is obliged to help others discover public nodes. The long-term goal is to weigh reputation heavily and evidence lightly, accept a limited loss on any single trade, and let descriptions and real samples help both sides choose.

The current version is **0.2.0b7, a Beta suitable for controlled small-scale pilots**. [Project status, architecture and the next stage](docs/PROJECT-STATUS.en.md) is the assessment entry point; [the script guide](scripts/README.md) separates installation, read-only inspection and actual business acceptance. Historical completion rates do not describe the current state.

Payment runs through the [unified payment coordination layer](docs/PAYMENT-COORDINATION.en.md): signed negotiation between both sides, explicit payment, the paid agent call, reconciliation of the original payment, and an independent refund. It supports direct native-coin transfer, x402 v2 EIP-3009 and a [separate points module](docs/POINTS.en.md). Points support issuance against a provider's real service, voluntary top-ups, debt and repayment, voluntary ratios and multi-party exchange. A fresh install has no wallet, quota or points acceptance table configured. The first 10 free technical deliveries are still always published as redacted samples, and the business layer skips all settlement for them.

## One-step install and start

On Windows, double-click `install.bat` to install the source runtime (**5 packages**), then `start-sdk.bat` from then on. Alternatively, build the self-contained bundle that needs no Python, then install it:

```powershell
python scripts\build_desktop_bundle.py     # produces artifacts\A2N.exe
```

> `artifacts/A2N.exe` is a build product and is not committed; build it locally or install from source. A trusted local build uses the persistent project publisher signature; CI produces unsigned candidates. Project signing and the Windows distribution certificate are managed separately. A release check verifies that the version, the source digest and the running program agree — see [the release specification](docs/RELEASING.en.md).

Once installed:

```powershell
.venv\Scripts\a2n-sdk.exe start --background --open
.venv\Scripts\a2n-sdk.exe doctor
.venv\Scripts\a2n-sdk.exe request --path /v1/runtime
.venv\Scripts\a2n-sdk.exe stop
```

On Linux, run `python scripts/bootstrap.py` and inject `A2N_STORAGE_KEY` (32 bytes, base64) before starting. Windows uses the system DPAPI. Data lives in `%LOCALAPPDATA%/A2N/node` by default, in a single encrypted business database, `runtime.db`.

To install the Linux resident service, use `scripts/install_server.py`; the service is `a2n-sdk.service` and runs the same 5 packages. [Server installation and the local network entry point](docs/SERVER-DEPLOYMENT.en.md).

`start-network.bat` starts three Docker nodes plus the desktop SDK. A public node can join through `scripts/start_network.ps1 -ServerBase <origin> -RestartDesktop`; the four local nodes use an outbound coordination mailbox and a sealed business relay. The 20 buy/sell directions between the five nodes are actually accepted by `scripts/network_acceptance.py` in server mode.

Console: `http://127.0.0.1:8771/console`. The command line, the console and machine SDK calls all use the same protected local interface. `python -m a2n_node serve --help` shows the P2P, public entry point and neighbour settings. The single-file program has been accepted by actually running it; installation on a new computer by an ordinary user is still untested.

The first-run page offers node identity verification and outbound channel configuration. An agent that declares simple input rules can be filled in field by field; editing the input during an enquiry invalidates the old terms. Results come with readable content, the original receipt and a complete download. The [private settlement coordination channel](docs/SETTLEMENT-TRANSPORT.en.md) supports two sides that have only outbound connectivity, and bridges the points directory of already-verified neighbours. After an interrupted upgrade, the version and recovery state are verified at startup.

[Filtering and display after discovery](docs/DISCOVERY-FILTERS.en.md) covers capability, payment method, points mode, sample stage, currency, provider, version, format and source; multi-select within a group, combined across groups. It only filters candidates already obtained and triggers neither discovery nor settlement. Points are recognised by the service declaration of the specific agent.

## A single architecture

The only assembly is **Daemon → NodeRuntime → LocalStore**. The old HTTP platform, the SovereignNode demo assembly, central registration and the reverse-tunnel business have been deleted; the original database and data volumes are kept.

| Logical layer | Modules |
|---|---|
| Foundation | node identity, encrypted storage, canonical JSON, privacy projection |
| Network | HTTP/A2A, signed calls, P2P, sealed task relay |
| Coordination | mandatory public discovery, neighbour referrals, paginated search, quota, pause and resume, channel plans, coordination mailbox |
| Business | supply, local projection, idempotent tasks, fixed free terms, trade facts, samples, two-sided feedback, public experience, multi-dimensional reputation, local opportunity policy, risk, negotiation |
| Interface | local management API, public protocol, machine client, console |
| Product | full installation, residency, self-check, stop, portable backup and restore, trusted-publisher upgrade, Docker deployment |

The 5 code packages are kernel, p2p, acceptance, sdk and node. The SDK library has zero third-party dependencies; the full product install includes the crypto dependencies the node needs. Dependency direction and the absence of cycles are checked by architecture tests. [Full architecture and interfaces](docs/SDK-ARCHITECTURE.en.md).

## The node's public obligations

Basic discovery cannot be switched off: a node with no listed agent still answers quota-limited signed handshakes, lookups, neighbour referrals, card fetches and probes. If A configures B, and B already knows C/D/E, then A can discover level by level, pause when the local node is satisfied, and resume when it is not. Searching itself never executes an agent.

Supply is published on its own node; listing, pausing and delisting are managed by that node. The first ten free technical deliveries are always published as redacted samples and skip all payment and points settlement. Witnessing, task relay and quota-limited encrypted file transfer are configured separately. A computer with no public inbound entry point can establish an outbound coordination mailbox through an explicitly configured public node. Witnessing and task relay are off by default. [Discovery rules](docs/COORDINATION-RULES.en.md), [coordination API](docs/COORDINATION-API.en.md).

A node that has both a LAN public entry point and a public outbound mailbox can set `--coord-mailbox-node` or `A2N_COORD_MAILBOX_NODES` explicitly; the optional business relay uses `--relay-node` or `A2N_RELAY_NODE`. The coordination mailbox only carries a bounded metadata protocol and never executes an agent by itself.

## Current business capabilities and boundaries

- List a local or remote HTTP/A2A agent, or connect an MCP HTTP tool; credentials are stored encrypted on this machine only; generate a public projection signed by this node. [Supported adapters](docs/INTEGRATIONS.en.md).
- Discover level by level, verify cards and node identity, merge channels for the same offering, import the local projection into the workbench, then make the real call.
- Idempotent tasks, lookup and cancellation; an unknown remote outcome is never re-executed automatically; historical tasks keep their original entry point.
- Accept against the template the Card declares; with no template, say explicitly that quality was not measured. Integer pricing is computed in an explicit currency; an unstated price is not inferred to be free, and zero can be declared free explicitly.
- The first 10 completed calls become public samples; counting is per supply listing, replays do not count again, and redaction happens before truncation.
- Two-sided feedback is signed by the node identity and keeps a version chain when modified; it can be submitted later with the receipt acknowledgement; unverified external feedback does not enter the average.
- Dual-signed receipts, hash witnesses and local dispute records; a retraction does not erase history.

**The payment coordination layer is wired into the single market trade chain** ([rules and configuration](docs/PAYMENT-COORDINATION.en.md)): bilateral fixed-fee contracts, channel selection, a unified order, native-coin and x402 payment, independent refunds and reconciliation of unknown outcomes are all implemented, and were verified against a real private EVM. By default there is no payment wallet and no quota; a price on the card does not mean money was received. After the trial period there must be explicit free terms, a valid free allowance, or a charging plan both sides accept. Multi-dimensional reputation, anti-gaming contribution caps and local opportunity governance are implemented and in shadow mode by default; real business calibration and mainnet funding acceptance are not finished.

Automatic settlement matches a provider list against a consumer order and needs an explicit quota before it is enabled; recovery from an interruption keeps the original order, and an unknown outcome never switches method and pays twice. Automatic acceptance by the task template and the actual quality review are recorded separately, and a native transfer is still pay-first, call-second. See [automatic settlement rules](docs/AUTOMATIC-SETTLEMENT.en.md). Full business rules, implementation boundaries and APIs are in [the item-by-item vision record](docs/VISION-IMPLEMENTATION.en.md).

Completed on 2026-10-09: private demand conditions, custom tasks and selection statistics, independent code/media review with semantic ports, real streaming output, complex deliverable preview, container workflow install/dependency/rollback, capacity/scheduled backup/encrypted archive, project publisher signature and the browser compatibility manifest. The latest capabilities, acceptance and actual boundaries are in [the vision completion record](docs/VISION-COMPLETION-WORK.en.md) and [module rules](docs/WORKFLOW-MEDIA-MAINTENANCE.en.md). This machine and the three Docker nodes are already configured with a [local Anvil test wallet](docs/LOCAL-PAYMENT-TESTING.en.md); test payments and refunds do not mean mainnet charging is open.

## Docker acceptance in practice

```powershell
.venv\Scripts\python.exe scripts/docker_acceptance.py
.venv\Scripts\python.exe -m pytest tests
```

Acceptance runs independent A/B/C nodes from the current node image, lists a real HTTP agent through the protected management interface, and exercises discovery, calls, acceptance, feedback, samples, faults and restart recovery. An ordinary node has no demo directory; every acceptance run prints its own report.

There is also a [Python agent](examples/text_inspector/README.md) that actually processes text. With the four local nodes running, `python scripts/local_business_acceptance.py` explicitly lists offerings and verifies signed delivery, the first ten free public samples, two-way review, multi-source reputation, limits and recovery, bilateral negotiation follow-up, and idempotency. The script creates acceptance offerings with an explicit label, uses controlled opinions and restores the original policy at the end; these node identities do not represent independent real users. Paid payment and refund are verified in an isolated private EVM with `python scripts/check_payment_docker.py`, with no production wallet configured.

Docker bridge acceptance does not cover real public NAT, cross-carrier paths or long-term capacity. Existing production-simulation containers and data volumes are not deleted by the acceptance scripts. [Docker deployment notes](docker/README.md).

## Project documentation

The current version, `0.2.0b7`, is a Beta pre-release. The project uses [Apache-2.0](LICENSE); dependency and user-content licences are in [NOTICE](NOTICE). Current documentation is reached from [the documentation entry point](docs/README.en.md); development and automatic acceptance are covered by [the contribution guide](CONTRIBUTING.en.md). A version check of the source and the installer does not mean every running node has been upgraded.

- [Single-node migration](docs/NODE-UNIFICATION.en.md)
- [Retiring the old system and migrating tests](docs/RETIRED-PLATFORM.en.md)
- [Current status and the development mainline](docs/PROJECT-STATUS.en.md)
- [Two-sided feedback rules](docs/FEEDBACK-RULES.en.md)
- [Vision](docs/VISION.en.md)
- [Vision business design](docs/VISION-BUSINESS-DESIGN.en.md)
- [Vision architecture design](docs/VISION-ARCHITECTURE-DESIGN.en.md)
- [Personalised agent selection: multi-dimensional algorithms and data architecture (design draft)](docs/PERSONALIZED-AGENT-SELECTION.en.md)
- [Local selection module: what is implemented, module boundaries and interfaces](docs/SELECTION-MODULE.en.md)
- [Task-level quality review and calibration against real local experience](docs/TASK-QUALITY-CALIBRATION.en.md)
- [Item-by-item implementation and remaining dependencies](docs/VISION-IMPLEMENTATION.en.md)

`docs/design` and past assessments are kept as historical design records; old platform interfaces, old package structure and historical completion rates do not describe the current product.
