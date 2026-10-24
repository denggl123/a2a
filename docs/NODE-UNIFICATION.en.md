<!-- source: 89523fbfb6c1 -->
# Node business unification migration

Date: 2026-10-05. The product has exactly one business assembly: `Daemon → NodeRuntime → LocalStore(runtime.db)`. Every node is at once a buyer workbench, a seller marketplace and a public discovery node.

## Migration scope

| Former capability | Where it lands after unification | Treatment |
|---|---|---|
| Card shape, whole-card hash, node identity signature verification | SDK card rules + node signing adapter | Rules kept; dependency on the platform registry removed |
| Template deviation, objective acceptance | Acceptance library + `DeclaredAcceptance` | Recomputable algorithm kept |
| Listing, pausing, delisting, stable goods identity | `RuntimeManagement`, local supply projection | Direct management of this node's public supply |
| Discovery, favourites, tasks and idempotency, late results, cancellation | Coordination service, `NodeRuntime`, `CallService` | The single business chain |
| First-ten-free history, privacy projection | `TrialBook`, public samples | One real delivery ledger |
| Mutual feedback, version chain, lightweight disputes | `FeedbackBook`, `DisputeBook`, mutually signed receipts | Existing node implementation kept |
| Objective performance, quality deviation and usage reviews shown separately | Node quality fact projection | Derived from local calls and verified feedback |
| Currency precision, integer pricing, pluggable settlement | SDK pricing and settlement ports | Pure rules migrated; real payment drivers wired in separately |
| Central registry, central task pool, platform reverse tunnel, demo top-ups and arbitration refunds | — | Old business assembly and its dedicated interfaces deleted |
| `SovereignNode`, demo scripts and the standalone demo console | — | Deleted; tests run the same node Docker image |

Old databases, node volumes and existing identities are outside the scope of this source cleanup. Fund balances in the old central database are not interpreted as payable balances of the new node.

## Public duties

Basic coordination is always provided. Every online node accepts bounded signed handshakes, lookups, paged referrals, Card fetches and metadata probes. A node stores and introduces verified, unexpired public neighbours and supply facts, and helps other nodes discover level by level. Network maintenance does not call an Agent. Business acceptance, public samples, witnessing and task relay are managed separately; basic discovery must not be switched off by delisting a business.

## Verification requirements

Run mutually independent nodes and persistent volumes from the same Docker image, mount HTTP/A2A Agents through the normal management interface and list them. Verify level-by-level discovery, pause and resume, import and refresh, real calls, repeated calls not executing twice, acceptance failure, the first-ten samples, mutual feedback, disputes and withdrawal, protection of public services, node restart and delisting.

The test report records the actual environment and the result of each check. A Docker bridge network cannot prove that real cross-internet access, different NATs and long-running capacity have passed.
