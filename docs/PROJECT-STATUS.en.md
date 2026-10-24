<!-- source: 968e14c4e66a -->
# Current project status and the development mainline

Updated: 2026-10-11; version: 0.2.0b7, Beta. This page is the current-status entry point; historical designs and acceptance records keep their original dates. Current inspection evidence is generated into `artifacts/project-health.json`, and the test and deployment results of each development round are recorded separately.

## 1. Product position and value

A2N is a peer-to-peer agent supply-and-demand network with zero platform commission. Every complete node has supply, demand, discovery, trade and local governance capabilities, and carries a quota-limited obligation to help public discovery.

Its value is making an individual's small tools and existing agents into services that can be discovered, tried and exchanged: samples lower the cost of trying, two-sided reputation lowers the cost of judging, points provide voluntary reciprocity, and open protocols reduce dependence on a single central directory. Reviews and limited dispute handling let unreliable services be phased out over time; nothing here promises that every single trade is perfectly fair.

The technical business loop is in place. Social value still needs real supply and demand, continuous service and independent user feedback to verify; controlled engineering calls prove the features work, they do not prove that a trustworthy and thriving market has formed.

## 2. The single business mainline

```text
public discovery level by level → merge candidates locally → private condition filters → personal ranking
→ explicit selection, import projection → fix the task/quote
→ the first ten samples skip all settlement; after that, settle by matching both sides' terms
→ deliver the original task → template acceptance, task quality review
→ two-sided feedback, limited dispute → update local reputation / selection effects
```

The provider configures the supported methods; the consumer configures the order of methods, the amount, the fee cap and automatic authorisation. The payment coordination layer fixes the original plan and calls the separate native-coin, x402 or points module. Native coin is pay-first, call-second; points follow the service delivery rules; x402 follows that module's authorisation and receipt flow. Different methods must not be read as the same arrival semantics.

The first ten actual free technical deliveries are always published as redacted samples and skip all funding and points settlement. Lookups, discovery, replays and failed deliveries do not create new samples. Successful delivery, quality review, funding confirmation, refund and dispute are recorded separately. An unknown outcome caused by a network fault only restores the original task/order; it never switches payment method, pays a second time, or re-executes the agent.

The old central platform and the old finance/reputation packages are retired; a few old URLs are compatibility entry points to the same current business service. There is no second trade ledger under maintenance.

## 3. Six layers of responsibility and the package boundaries

| Layer | Current responsibility and main modules | Boundary |
|---|---|---|
| Foundation | identity and signatures, canonical serialisation, encrypted storage, redaction, integer pricing | does not decide samples, reputation or payment eligibility |
| Network | HTTP/A2A transport, P2P observation, direct connections, task/file relay | returns connection and transport facts, does no personal ranking |
| Coordination | `coordination` search/referral/session/quota/channel; `payment_coordination` term negotiation/fixed plan/recovery | discovery does not execute an agent; payment is triggered by the business layer, which calls a separate submodule |
| Business | `TradeService`, `AutomaticTrade`, `calls`, samples, feedback, disputes, reputation policy; `selection`, `task_quality`, `calibration`, private learning; workflows and deliverable assets | free eligibility is decided here; evaluation reads data through fact ports and never reads a wallet ledger to decide ranking |
| Interface | protected local API, public signed protocol, `NodeClient`, console, A2A/JSON/MCP adapters | adapts protocols and reuses the same business chain |
| Product assembly | `Daemon`, desktop install/self-check/residency, backup and restore, signed upgrade, Docker | injects execution/network/payment ports and manages the lifecycle |

Native coin, x402 and points are separate implementation modules under payment coordination and do not add a new top-level business system. The dependency direction of the five packages is `kernel → p2p/acceptance → sdk → node`; that expresses dependency level only — the SDK library still depends on the standard library alone, and node injects the concrete implementations. Architecture tests check cross-package dependencies and cycles; keeping port boundaries between application services inside the same package remains a human responsibility.

The detailed contracts are in [architecture](SDK-ARCHITECTURE.en.md), [coordination](COORDINATION-API.en.md), [selection](SELECTION-MODULE.en.md) and [payment](PAYMENT-COORDINATION.en.md).

## 4. Where the data lives

| Data | Ownership and retention | External scope |
|---|---|---|
| Private keys, wallet/accounts, raw requests, original task entry points, orders and the points ledger | encrypted `runtime.db` on the owning node; backups are encrypted too | not public; the payment protocol exchanges only the necessary authorisation and signature facts |
| Supply cards, first ten samples | the provider keeps the real delivery, plus a public projection, a redacted summary and a restricted preview | publicly discoverable; delisting does not erase samples already produced |
| Two-sided feedback, receipts, negotiation and disputes | the original facts and version chain live on the nodes involved; public experience follows the publication rules | only verified facts with a known source are used; a dispute is not a finding of fraud |
| Network/latency/quality/credit and other multi-dimensional material | raw local observations are kept apart from the verification cache; normalisation, confidence and data age are computed in the selection module | personal ranking, weights and exact budgets stay local |
| Task quality labels, calibration, private learning | consent, source, retraction and time window are traceable; adopting a suggestion requires re-checking the version | engineering and assistant records never pose as real people; another party's public credit or payment policy is never changed |
| Complex deliverables, workflows, backups | governed by permission, capacity and retention policy; historical facts are never treated as garbage | a private deliverable needs an authorisation; samples never expose the raw deliverable files |

## 5. A realistic judgement of completeness

| Area | Judgement | Still awaiting real verification |
|---|---|---|
| Single-market loop, mandatory public samples | implemented, verified by real cross-node calls | long-term real supply and demand, and user experience |
| Discovery, post-discovery filtering, personal ranking | sessions, budget, filtering and eight-dimension evaluation are wired in | real paths across the public internet and NAT, scale with unknown nodes, sustained path quality |
| Two-way reputation, disputes, task quality | signed feedback, versions, lightweight negotiation and independent code/media review are implemented | the cost of malicious multiple identities, disagreement between reviews, real quality labels; reputation governance runs in shadow mode by default |
| Automatic payment and points | matching, authorisation, fixed plans, three methods, real local test coins, recovery and independent refunds are implemented | mainnet funding audit, a real payment provider; real multi-party exchange at scale |
| Calibration and private learning | data thresholds, held-out validation, consent and retraction are implemented | qualifying independent human data is still scarce; assistant calls cannot be padded into real user samples |
| Agent integration and complex deliverables | A2A, JSON HTTP, restricted MCP HTTP, signed container workflows, streaming output and assets are implemented | full cross-protocol interoperability, more third-party agents; container execution and some independent reviews need Docker on the machine |
| Complete installation and release | the full Windows program, the server source package, signature/hash/version consistency, self-check and upgrade are implemented | the install experience on a new computer, an OS distribution certificate, public distribution channels; a GitHub CI configuration exists, and only the remote result counts |

Overall position: **a functional Beta usable for controlled small-scale pilots**. The local loop is fairly mature; public-network operation, governance of unknown participants and long-term data calibration are less mature. The number of unit tests does not convert into an "overall completion percentage".

This machine should currently be the Windows desktop node on 8771 plus three Docker nodes on 18881/18882/18883; there are also test agents, a test chain and a facilitator, which are not market nodes. The actual ports, identities and source consistency are whatever the inspection says. A historical controlled order may still sit at "payment confirmed, delivery unknown"; it will not be re-paid, re-run or given a fabricated delivery just to clear the count — the inspection lists those orders awaiting reconciliation separately.

A complete node has been added on Tencent Cloud at `43.143.214.103`; public entry point 8891 has been confirmed open by the user, and the management entry point remains the server loopback on 8771. A real text agent is deployed and published; the actual progress of five-node discovery, sealed relay, points and original-order recovery is in [this deployment report](../artifacts/SERVER-DEPLOYMENT-2026-10-11.md). The first-round failure record is kept, and an installation self-check cannot substitute for public-network acceptance.

`0.2.0b4` fixed the missing original-task query id when the first reply on the bilateral signed path was lost. The general SDK stays compatible with old records through a separate resolution port injected by the node; an ordinary third-party A2A peer never guesses a task id; recovery only queries the original task, and still requires a valid signature matching the original request.

`0.2.0b5` fixed steady memory growth caused by Windows DPAPI creating a native type on every decryption. The existing encrypted data format stays compatible; repeated and concurrent decryption is covered by a regression on real Windows resources; the five-node deployment and cross-machine business evidence are whatever this round's independent acceptance report says.

`0.2.0b6` stores the complete signed card declared by the directory snapshot, so refreshing a mailbox lease no longer loses the card a digest points to; an expired cursor re-reads a limited number of times within the original quota. The cache is capped at 512 entries, 8 MiB and 300 seconds, and coordination operations still never execute an agent or take part in settlement.

`0.2.0b7` merges parallel changes to the launcher and the backup tool. Process protection is assembled by the product entry point: Windows caps a single process and its whole process tree, Linux keeps the original public-service configuration, and a child process created by an upgrade is not killed when the protection handle closes; the old scheduled-task entry point resolves the installed SDK dynamically. A snapshot is fully content-verified and placed atomically — see [backup and restore rules](runbook-anvil-snapshot-restore.md).

## 6. This round's cleanup and the support entry points

This round removed a test directory and dependencies that had been committed by mistake, along with demo checks for the already-deleted old platform, and fixed stale image and fixed-IP entry points, acceptance results being reused from history by default, admission of invalid records in automatic calls, and stale markers after recovery. The automatic order summary now counts every unfinished record, so a list of the most recent 100 no longer hides older orders. Test services are part of a rebuildable Compose configuration, which keeps node business data and the images still used by workflows.

The current split of tools is in [the script entry point](../scripts/README.md) and [Docker deployment](../docker/README.md). Installation, inspection, source acceptance, isolated-node acceptance and explicit installed-node acceptance each have a clear job. Historical reports keep their original dates and are not evidence that this round passed.

## 7. The order of the next stage

1. **Small real pilot**: services with genuine demand, explicit reviews from real people, and retraction; track search through selection, delivery, objection and reuse, and never invent dates or identities to reach a calibration threshold.
2. **Public network and reliability**: independent computers, carriers and NAT; verify level-by-level discovery, the mailbox, path changes, reconnection after a drop, backup restore and long-running operation; handle unfinished original orders against the real evidence.
3. **Governance verification**: dispute support volume, contribution caps on repeat relationships, and multi-identity attacks; watch for collateral damage before deciding whether to take opportunity limits out of shadow mode.
4. **Open integration and distribution**: pick a real third-party agent for interoperability verification, run remote CI, accept the install experience on a new computer, then publish; audit funding security only when mainnet payment is needed.

The main work of the next stage is trustworthy operation and user validation. Adding another payment theory, rebuilding a second platform, or forcing a personalisation model to start on assistant data would all drift away from the current mainline.
