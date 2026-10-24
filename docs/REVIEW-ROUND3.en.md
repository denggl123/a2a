<!-- source: c94107241d3b -->
# A2N architecture review · round 3 (whole → parts)

Method: four parallel read-only explorations (data flow and consistency / interfaces and dependency boundaries / security and compliance / observability and documentation),
then each item checked against the code before deciding whether to fix it. **This round prioritised the money red lines**; every red-line issue was fixed and guard tests added.

## Overall judgement

| Dimension | Previous round | This round | Notes |
|------|-------|------|------|
| Architectural layering and decoupling | 8 | **9** | The extension-point registry took shape after the four-way split; no circular dependencies found, kernel has zero dependencies and the SDK uses only the standard library |
| Data consistency | 7 | **8.5** | Settlement and terminal state are now in the same transaction; remaining publish ordering and routes writing tables directly are not cleaned up |
| Fund safety and compliance | 6 | **8.5** | The x402 criterion and the mint gate are fixed; cross-currency reconciliation is not yet split by currency |
| Observability and documentation | 7 | **8** | The three latency bases landed and are labelled; console currency display and event coverage have gaps |
| **Overall** | **7/10** | **8.5/10** | Red lines closed; what remains is engineering debt that "should be done but can queue" |

### Three things that still hold

1. **The three iron rules have not been bypassed**: no mint side door (and there is now a gate), money moves only through the custodian port, and all accounts are integers.
2. **Extension points are registry-based**: settlement methods, acceptance policies and aggregation policies need no change to the dispatching subject.
3. **Data ownership is clear**: declarations belong to the server, bookkeeping to the platform, and measurements to whoever measured (the four latency bases).

## P0 · Fixed this round (money red lines)

| # | Problem | Fix |
|---|------|------|
| 1 | The x402 criterion came from `payment`'s own `maxAmountRequired` — **the caller decided how much to ask for** | The challenge body is now built from the server's market price |
| 2 | `mock.verify_payment` read the top-level `maxAmountRequired` while the amount is in `accepts[0]` — **need was always 0, so any credential passed** | Read the x402-shaped `accepts[0]` |
| 3 | `resolve` looked up the price with an empty skill, and the price book is never found by a skill key — **the challenge amount degenerated to 1 (i.e. free)** | `resolve` accepts a skill and defaults to the first skill on the card |
| 4 | `/v1/custodian/deposit` could mint with no authentication (the route itself is whitelisted) | A licensed party's signature is now required; demo mode needs an explicit `A2N_DEMO_CUSTODIAN=1` |
| 5 | `submit` committed in three separate stages (split / count / terminal state) — **a crash left "money moved, task stuck in ACCEPTED", and a retry settled twice** | The settlement stage is wrapped in `store.tx()`, and the three writes hand over with `commit` |

## P1 · Fixed this round

| # | Problem | Fix |
|---|------|------|
| 6 | A2A cancellation used the task's `requester_id` — knowing the task_id let anyone cancel another's task and trigger a refund | Check that the caller == the initiator |
| 7 | `heartbeat` read-modify-wrote connection without a transaction, and overwrote each other concurrently with `observe` | Wrapped in `tx()` |
| 8 | `MockCustodian.deposit` lacked the `currency` parameter, mismatching the port signature | Added, with the currency recorded in the ledger note |

## Backlog (in suggested order; none affects current correctness)

| # | Problem | Location | Suggestion |
|---|------|------|------|
| A | The routing layer writes `a2a_tasks` directly (and never updates it afterwards, splitting it from the tasks fact) | `routers/a2a.py:155` | Move down into a2n-task and sync with state |
| B | The routing layer writes `rosters` directly (the table belongs to registry) | `routers/registry.py:_save_roster` | Have registry provide a write interface |
| C | In a2n-task, `publish` happens after `commit` (violating the same-transaction event convention) | `a2n_task/service.py:128,247,262` | publish before commit |
| D | Reputation events never take effect: nobody subscribes to `node.suspended` / `arbitration.lost` | `a2n_reputation/service.py:17-23` | Wire up the event bus or fix the event names |
| E | `balance_fen` adds USDC as if it were CNY across currencies, polluting the custody total | `a2n_settlement/reconcile.py:17` | Keep balances per currency and reconcile by currency |
| F | console `yuan()` hardcodes ¥, so multi-currency tasks are mislabelled | `console.html:102,554,1041` | Format by `currency` |
| G | Many bare `ValueError`s in the routing layer (no global exception handler → 500) | consensus/a2a and others | Move to domain exceptions + `except A2NError` |
| H | Policy (`set_policy`/`SETTLERS`/`register_strategy`) is process-global and drifts across workers | three places | Central registry or persist to the database; acceptable for single-process deployment |
| I | Observation reports without a `task_id` can be spammed (affecting the discovery page's "consumer" basis) | `registry.observe` | Make task binding mandatory in production |
| J | The console and the SDK implement the aggregation policy twice | `console.html` / `aggregate.py` | The sync obligation is already annotated; a shared module can be extracted |
| K | The discovery page does not show the declared SLA value | console | Add a column to complete the four bases |
| L | SSRF: `probe_inbound` resolves after allowing a domain, so DNS rebinding can bypass it | `reachability.py:51-68,101` | Re-check the IP after resolution + ban redirects + outbound proxy |

## Conclusion

- **Ready for the next phase**: with the red lines closed (payment criterion, issuance gate, settlement atomicity), the credibility of the accounts holds up.
- **Next round should prioritise A/B/C/D**: all four are "table ownership and event consistency", which is what moves the architecture from "runs correctly" to "does not lie even when it crashes".
- **E/F belong to the multi-currency close-out**: before doing them, decide "whether the platform keeps accounts per currency" — that is a product decision, not a code decision.
