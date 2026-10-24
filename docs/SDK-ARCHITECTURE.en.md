<!-- source: ce5e695f4649 -->
> Current status and the full module map are in [PROJECT-STATUS](PROJECT-STATUS.en.md). This page describes product boundaries; past deployment numbers are history, and the current installer and actual nodes must be confirmed by inspection.

# The complete desktop SDK and its module boundaries

Updated: 2026-10-11. "SDK" here means the complete A2N node product installed on a computer.

## 1. Product entry points

On Windows, double-click `install.bat` in the repository root:

1. Check for Python 3.11+; if it is missing, install a user-level Python 3.13 through winget from Windows App Installer.
2. Create `.venv` and install the third-party dependencies plus all 5 local packages.
3. Run the full product self-check: package imports, system credential protection, a temporary node, local HTTP, coordination signatures.
4. Start the node in the background and open `http://127.0.0.1:8771/console`.

After that, double-click `start-sdk.bat`. Command entry points:

```powershell
.venv\Scripts\a2n-sdk.exe start --background --open
.venv\Scripts\a2n-sdk.exe doctor
```

`a2n-node serve`, `python scripts/bootstrap.py` and the old install scripts remain compatible; the old install scripts hand over to bootstrap. `--check` runs the full self-check, `--dry-run` installs nothing, and `--launch` starts after installing.

Those are source-install entry points and need to download dependencies. The standalone program `artifacts/A2N.exe` bundles the runtime and all five packages; double-clicking it opens an install and offline-recovery page, and it can register a start-at-login entry for the current user; when the system refuses to modify an existing scheduled task, use the current-user login entry point instead. There is no system tray and no MSI yet. Linux and containers still configure storage protection through `A2N_STORAGE_KEY`.

The default data directory is `%LOCALAPPDATA%\A2N\node`, and the background log is `node.log`. An upgrade keeps identity, supply, accounts, calls, samples, feedback and local credentials. The launcher checks which node directory the port belongs to, so it does not open a second instance.

For desktop launches from the same directory, `desktop-launcher.lock` elects a single launcher; when a scheduled task, a login launch and the installer start repeatedly, they wait for the original node to become ready and then exit normally. Background launches go through `a2n_node.desktop` by default, with or without `network.json`. `runtime.lock` is still held by the Daemon until a complete shutdown, protecting encrypted storage and offline recovery; the launcher no longer decides that it may start by briefly acquiring and releasing the storage lock. After the old node stops, the launcher lock is released first, and only then is the installed new program verified and taken over. If a local entry point is still not ready after 30 seconds, it logs and returns a non-zero status; genuine start errors and port-ownership errors keep reporting. The compatible scheduled-task script retries abnormal exits only, and exits after a normal reuse or a deliberate stop.

A portable install can keep `.a2n-local.json` in its launch directory, containing `{"home":"absolute node directory"}`; the `A2N_HOME` environment variable takes precedence. Console launch, the CLI and the Windows scheduled task read the same configuration from the same launch directory. The current four-node environment uses the repository's `data/desktop-sdk`; the original SDK identity and 33 historical calls have been migrated in, and the pre-migration encrypted backup is kept. Desktop network settings live in `network.json` in the node directory, and an ordinary SDK restart keeps using them.

## 2. The six logical layers and the payment submodules

These modules are assembled in one resident process and connected through ports.

| Layer | Responsibility | Current modules |
|---|---|---|
| Foundation | identity, encryption, storage, canonical JSON, redaction | `a2n_p2p.Identity`, `a2n_node.protection`, `a2n_sdk.storage`, `serialization`, `privacy` |
| Network | bounded connections, transport, P2P observation, direct links and the sealed task relay | `coord_network`, `p2p_service`, `peer_transport`, `relay_transport`, `relay_service`, `relay_provider`, `adapters` |
| Coordination | public handshake, neighbour maintenance, paginated referrals, search sessions, quota, candidate merging, channel observation and plans; payment term negotiation, method selection, settlement orchestration and recovery | `coordination`, `coordination_service`, `coord_identity`, `coord_service`, `coord_neighbors`, `coord_mailbox`, `payment_coordination`, `payments`, `points_coordination`; Node `payment_service`, `points_payment` |
| Business | supply, favourites, fixed terms, facts, samples, feedback, experience, reputation, risk, local policy and negotiation, selection and learning; decides whether to start settlement | `runtime`, `calls`, `pipeline`, `trade_facts`, `contracts`, `trials`, `feedback`, `experience`, `reputation`, `policies`, `resolutions`, `reconnect`, `selection`, `task_quality`, `calibration`, `agent_packages`, `assets`; Node `trade_service`, `automatic_trade` |
| Separate payment submodules | each method's own funding or points rules; called by the coordination layer, never deciding business eligibility | SDK `points`; Node `points_service`, `evm_payment`, `x402/` |
| Interface | local protection, public protocol translation, console | `local_api`, `coordination_api`, `web/runtime.html`, `web/coordination.js`, `management` |
| Product assembly | full installation, residency, self-check, lifecycle, backup and restore, upgrade | `Daemon`, `product_cli`, `desktop`, `autostart`, `backups`, `upgrades`, `scripts/desktop_launcher.py`, `scripts/bootstrap.py` |

The dependency levels of the 5 retained packages (kernel=0, p2p/acceptance=1, sdk=2, node=3) are checked by `tests/test_package_layers.py`. The table above is split by runtime responsibility; the coordination contracts and application services inside the SDK package keep zero third-party dependencies, and node injects the identity and network implementations.

Implemented on 2026-10-08: native coin, x402 and points are all separate submodules called by payment coordination; a payment submodule is a subordinate implementation boundary of the coordination layer and does not add a top-level product. `TradeService` already carries free eligibility, accepting an order and making the call; the first ten samples skip all settlement and do not create a zero-amount order. Service issuance of points adds provider points held by the provider, and a voluntary top-up adds provider points held by the named consumer. `PointsPayment` handles voluntary routing, reservation across all parties, durable commit and recovery; `PointsBook` records only the issuer's own ledger. Rules, interfaces and implementation limits are in [the points module](POINTS.en.md).

The product main path is `a2n-sdk start → Daemon → NodeRuntime`, with data in the encrypted `runtime.db`. The old `SovereignNode`, the central HTTP platform and the registration and reverse-tunnel assemblies have been deleted; the original data files are kept, and a central finance balance is not interpreted as a node balance.

## 3. How coordination is implemented

### Local ports

`CoordinationService` implements `CoordinationPort`; `CandidatePolicy` implements `LocalCandidatePolicy`; `CoordinationNetwork` implements `CoordinationNetworkPort`.

Local HTTP paths:

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/coord/searches` | create a search |
| GET | `/v1/coord/searches/{id}` | status, control version and quota |
| GET | `/v1/coord/searches/{id}/candidates` | paginate by result version |
| POST | `/v1/coord/searches/{id}/pause` | stop dispatching new work |
| POST | `/v1/coord/searches/{id}/resume` | keep candidates and queue, add another round of budget |
| POST | `/v1/coord/searches/{id}/cancel` | end the session |
| POST | `/v1/coord/searches/{id}/evaluate` | evaluate against the local satisfaction policy |
| POST | `/v1/coord/searches/{id}/route-plan` | channel plan, optionally with a read-only probe |

A modifying request requires `Idempotency-Key`; everything except creation also requires `If-Match: "<control version>"`. A repeated command is replayed in preference; reusing the same key with different content returns 409. Progress updates and result updates carry their own version counters and do not consume the control version. Every local interface keeps the token, Host and Origin protections.

The default satisfaction policy accepts only verified candidates and supports provider and version conditions plus `preferences.min_candidates` (3 by default). An unknown required condition or policy id puts the search into a paused state that needs local input. Resuming from the console raises the target candidate count; the API's resume can submit new `preferences`. Private conditions and exact budgets stay on this machine.

### The public protocol

The six signed operations live at `/public/v1/coord/{hello,find,card,routes,probe,forward}`. The handshake verifies node identity and nonce; a referral verifies the declarations of both the introducer and the target node; a card fetch checks the signature, the offering key and the hash.

In the background a node maintains only explicitly configured public neighbours, refreshing signed declarations every 45 seconds, at most 3 per batch and at most 32 entry points. Maintenance sends HELLO only: it does not search on the user's behalf, does not walk referrals and does not call an agent. So when B has configured C/D/E, A can obtain continuously updated referrals from B.

FIND returns at most 10 items per page and at most 8 referrals, interleaving offerings and referrals. The search rotates across sources; the offering key is uniformly `(provider_did, service_id)`, and a card, channel and source for the same offering are kept separately. `service_id` in a supply projection is the real offering identifier.

The outbound coordination mailbox uses `/public/v1/coord/mailbox/{register,poll,reply}`. A node behind a LAN connects to one configured public node, holds a short lease, and answers signed coordination requests addressed to it. The parent node keeps the target's original response signature. Forwarding is currently limited to one explicit neighbour and does not accept a nested FORWARD; the coordination mailbox and the sealed task mailbox are managed separately.

### Persistence and resources

- Searches, queues, candidates, command results and quota go into the encrypted local database. A restart turns an interrupted search into a paused one, keeps the reserved consumption, and re-queues the interrupted read-only operations.
- Pausing lets in-flight requests finish; while an in-flight request or probe has not finished, resume returns BUSY, so its quota is not reset.
- Every network operation reserves request and byte quota first; unused bytes are returned after a complete response, while failures and interruptions keep the reservation. A probe reserves conservatively at one operation per channel, at most 64 KiB and 800 ms.
- Automatic resume requires its own total budget, at most 64 rounds. A manual resume also obeys the existing total budget.
- At most 4 searches run at once on this machine, and each session reaches the remote side serially; the maximum is 512 queued items, 256 sessions and 512 cached cards. Session TTL is 1 hour; node and channel declarations have a TTL of 120 seconds.
- A public single message is at most 256 KiB, and an identity may send at most 120 per minute; the mailbox holds at most 128 leases and at most 8 in-flight requests per target. These are the current pilot parameters.
- The new protocol uses a fixed DNS resolution address, verifies TLS, forbids redirects and system proxies, and checks for private addresses. A referral grants no access to an internal network; a LAN pilot needs an explicit `--coord-allow-network CIDR`. The old directory and UDP discovery keep compatibility adapters, and their capabilities are marked separately.
- The UDP compatibility discovery inside coordination asks one direct neighbour at a time and reads one card, without broadcasting or fetching cards concurrently; it walks at most 32 known direct neighbours. The compatibility path limits the actual read size and conservatively keeps the whole byte reservation instead of substituting the final card size for network consumption. Network heartbeats and explicit neighbour maintenance are independent of the search quota.

Searching, reading samples, probing and route planning never call an agent, never consume a trial, and never accept or settle anything.

## 4. Channels and how calls connect

`POST /v1/projections` accepts `search_id`, `key` and an optional `route_id`. The management service takes a verified, unexpired channel from the local session and does not accept a channel plan invented by the client. Importing is still an explicit business-side operation.

Routing defaults to local reachability observations, latency and a preference for direct links. The SDK's transport adapters consume those choices; only when a peer is confirmed unreachable may an approved alternate channel for the same offering be tried. Identity failures, business failures and timeouts are not retried automatically.

Creating a task persists the complete Card and the actual entry point. Lookup, cancellation and verification of a late receipt keep using that entry point; refreshing favourites does not migrate a task already accepted. A read-only metadata probe produces `control_reachable`; `task_reachable` stays unknown.

## 5. Public duties and migration

Basic discovery and free delivery samples are always on. The body of `POST /v1/public-services`, for example `{"services":{"witness":false,"task_relay":false}}`, configures the optional services separately; `discovery:false` or `samples:false` returns 400 and the whole configuration does not take effect. The old `/v1/public-service` master switch only controls witnessing and the task relay and cannot switch samples off. At startup, an old "samples off" setting is normalised to on; a delisted or uninstalled supply can still have its historical samples read by the original service_id. Witnessing, the task relay and file transfer are off by default; `blob_cache` is wired to a separate quota-limited encrypted file transfer and currently offers no persistent cache.

The old master switch remains a compatibility entry point for the optional services; an old explicit "off" configuration is kept but cannot switch off basic coordination. The console shows the switches separately. A machine that provides public services still needs a reachable entry point, and an ordinary LAN computer can establish an outbound coordination mailbox through a configured public node. With no neighbours it shows an isolated state.

## 6. What is actually finished

Installed and wired into the product: supply, favourite projections, signed calls, acceptance, bilateral receipts, trial samples, feedback and its version chain, lightweight disputes, coordination discovery and the mailbox, channel plans, the console, startup and self-check.

The old finance and reputation packages are retired. Currency, measurement dimensions and integer pricing have moved into the SDK; the general business entry points `/v1/trades/*` are wired to native coin, x402 and points. By default no real funding wallet is configured, and no points service policy or acceptance table for another party's points is enabled. `GET /v1/quality` returns local quality facts only; unverified external feedback does not enter the average. Two-way feedback is exchanged and fact summaries are offered; multi-dimensional reputation, anti-gaming contribution caps and local opportunity governance are implemented and run in shadow mode by default. The business candidate policy is wired into the coordination layer, and hard conditions, reputation support and newcomer opportunity are evaluated locally; real business calibration still needs a pilot.

Verification covers real HTTP and the signed protocol across local multi-node setups, partial network faults, pause and resume, quota, identity failures and call hand-off. Actual execution of the single-file program, signed upgrade, backup restore and the independent file NAT mailbox have been accepted on isolated nodes; real cross-internet paths, many kinds of NAT, long-running capacity and graphical installation on a new device still need verification, and local tests cannot substitute for them.

## 7. The single listing path and the machine interface

`POST /v1/bindings/http` accepts `listed` (true by default); `/v1/publish` and `/v1/unpublish` change this node's public state and tolerate repeats. `/v1/bindings/state` pauses or resumes taking orders, and `/v1/bindings/remove` uninstalls and leaves the directory. Delisting rejects new public calls, while historical tasks can still be queried by the original buyer. Old publication records are converted once at startup into listed metadata, and the original records are kept in migration_archive.

The machine client is `a2n_sdk.NodeClient` (Client is an alias of the same implementation) and only connects over loopback HTTP. The full product command `a2n-sdk request --home ... --port ... --method POST --path ... --body -` takes the current control token from the encrypted database; the token is renewed on every start. `a2n-sdk stop` stops cleanly through the protected API. The public proxy lets through the public protocol, dual-signed receipts and the sealed relay, and rejects the management page and API.
