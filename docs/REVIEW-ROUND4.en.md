<!-- source: ec3c66b1a713 -->
# A2N architecture review · round 4 (close-out: from "runs correctly" to "does not lie even when it crashes")

Method: check item by item against round 3's backlog (A–L), plus **issues exposed by a live simulated run** (31 smoke checks + a second round)
and a line-by-line code re-check before deciding whether to fix. **This round is organised around three threads: table ownership / event transactions / bookkeeping closure**,
and every fix must be able to produce evidence on a live database (all numbers in this report come from measurements on data/e2e_a2a.db).

> Important background: most of the fixes this round were not "designed debt" but **debt produced by running the thing** —
> the problems the smoke run and the second simulated round exposed (relay calls not booked, observations overwritten by heartbeats, self-reported TTFT always empty)
> were exactly the type that "tests are all green but only show up when you simulate". 252 passing tests are only a floor.

## Overall judgement

| Dimension | Round 3 | This round | Notes |
|------|------|------|------|
| Table ownership and data consistency | 8.5 | **9.5** | One owner per table; four tables connected; events in the same transaction as the business, notifications deferred until after commit |
| Governance chain completeness (every call passes through) | 8 | **9.5** | Relay calls have a closed bookkeeping loop (paid = peer deal / free = zero entries), and "having a credential means you need not book" is now blocked |
| Fund safety and compliance | 8.5 | **9** | Multi-currency split and isolation; what remains is audit-level hardening (SSRF, rate limiting) |
| Observability and documentation | 8 | **9** | All four latency bases now have data **on the only real chain**; CALL-PARAMS aligned with the black-box basis |
| SDK engineering quality | 7 | **9** | Signature gatekeeping, failures reported honestly, forwarded execution included in local observation |
| **Overall** | **8.5/10** | **9/10** | Three threads closed; everything remaining is "queueable engineering debt" that does not affect correctness |

## P0 · Fixed (this round)

| # | Problem | Fix | Live-database evidence |
|---|------|------|---------|
| 1 | **Relay calls were not booked**: once the credential arrived, a paid agent was called for free and the supplier worked for nothing | `_bill_relay_call`: paid + ACTIVE pairing → a peer deal in deals (task_id=None); free → zero entries; failure → not booked; the result travels in the `X-A2N-Billing` response **header**, keeping the A2A body pure | 6 relay calls in round two: bob 3 deals, carol 0; deals=4/charges=2/free 0 entries |
| 2 | **The SDK container node's handler signature was wrong** (`fn(path,payload)` mounted as a task handler) → a silent TypeError, tasks stuck in ASSIGNED forever | Split `handle`/`handle_http`; inspect the signature at startup and **fail at startup** if wrong | The whole smoke chain passed, no stuck orders |

## P1 · Fixed

| # | Problem | Fix |
|---|------|------|
| 3 | Peer exposure `exposure_fen` read-check-write race (exposure could be exceeded) | `record_peer` wrapped entirely in `tx()`, with the deal's three actions handing over with `commit=False` |
| 4 | `publish` after `commit` (a2n-task / dispute.resolve) → a crash in between separated events from facts | publish before commit (events go into a same-transaction outbox) |
| 5 | **A subscriber committed inside the publisher's transaction**: as soon as an event arrived it wrote its own database, committing the publisher's transaction early | `store.on_commit/on_rollback` hooks + a deferred notification queue for events: inside the transaction only enqueue, deliver after commit, discard on rollback (5 items in tests/test_events.py) |
| 6 | The routing layer wrote `a2a_tasks` (A) and `rosters` (B) directly — table ownership broken | `a2n-task.save_view()` becomes the owner of a2a_tasks (state is not copied, computed from tasks on read); a new `a2n-registry.rosters` module absorbs reads, writes and p2p merging |
| 7 | Multi-currency contamination: custodian_book had no currency column, and `balance_fen` added USDC as if it were "cents" | Add a `currency` column; `balance_fen` anchors CNY only, with a new `balance_of(cur)`; the reconciliation document states that it anchors CNY only |
| 8 | Reputation events never took effect: nobody subscribed to `node.suspended` / `arbitration.lost` | wiring now subscribes to both events |
| 9 | Global exceptions ran naked: A2NError produced 500, bare ValueError leaked internals | `@app.exception_handler`: A2NError→its own http_status; fallback 500 with redaction |
| 10 | Terminal-state definitions were muddled: "completed" counted REJECTED too; GMV mixed in non-points | Completed = SETTLED+ACCEPTED; GMV/points count only SETTLED; console wording aligned |

## Exposed and fixed by this round's smoke run (tests green, only simulation showed them)

| # | Problem | Root cause | Fix | Live-database evidence |
|---|------|------|------|---------|
| 11 | **Consumer observations overwritten by the heartbeat**: 6 observation reports returned 200 but only 3 remained in the database | `heartbeat` read connection **outside** `tx()` — while `BEGIN IMMEDIATE` waited for the lock, observe committed and the heartbeat wrote back a stale copy. The waiting window is the whole length of the other transaction, not microseconds | Move the row read inside the lock (`observe` was already inside it, so the two writers are now genuinely mutually exclusive) | Before the fix 6 lost 3; after the fix 3+3 all survive (with a deterministic regression test: temporarily defer the tx until after the concurrent writer, which must fail on the old code) |
| 12 | **Self-reported TTFT always empty**: the design existed, the parameters existed, the sample count was always 0 | The v1 main chain is orchestration-layer **channel forwarding + platform-submitted delivery** (delivery=inline), so the task push channel is almost never triggered; the SDK only recorded on the push channel | `TunnelClient.on_execute` callback: forwarded execution (success/failure, duration, skill) enters the local observation window (TTFT = the moment the result is ready, consistent with the push channel's non-streaming basis) | bob 5 samples 304ms / carol 4 samples 301ms / erin 1 sample 305ms (all 0 before the fix) |

## SDK / console / documentation (closed out in the same round)

| # | Item | Notes |
|---|------|------|
| 13 | `call_agent` re-fetches a credential once on 401 | An expired credential no longer interrupts the consumer |
| 14 | The SDK gained `fail()/pay_channels()/pay_methods()/bind_pay_method()/close_pay_method()/pay_compatible()/charge()` | The node side no longer needs hand-written HTTP |
| 15 | `gpu_seconds` removed from SDK metering | A capability has a black-box basis: the SDK cannot know whether a GPU was really used, so reporting it hard would pollute the basis |
| 16 | `connection_report` no longer judges direct by itself | Having a url ≠ direct (it may be an internal or placeholder address); leave it empty and let the platform judge by public reachability and source IP, while explicit declaration remains the node's right |
| 17 | console: `esc()` escapes quotes (XSS) / `curMoney` multi-currency formatting / `apiAll()` no blank screen when one source fails / SLA declaration column | All four bases now complete on the discovery page |
| 18 | docs/CALL-PARAMS.md aligned | Removed the "compute" declaration (after black-boxing only deployment.region remains); added the `X-A2N-Billing` billing header; added the region projection note |

## Verification (the source of every number in this report)

- **pytest: all 252 pass** (13 net new this round: events 5 / relay_billing 3 / multicurrency 2 / connection 3).
- **Cold-start simulation**: wipe the database → platform on 8000 + three nodes (9102 paid / 9103 free / 9104 x402) → 31 smoke checks pass
  (discovery → free direct call → paid gate → direct-payment credential → peer account → x402 pay-as-you-go) → round two (black-box projection / region filtering / consumer-side aggregation round robin, 6 in a row).
- **Live four-table check**: tasks=4 (ACCEPTED×4), deals=4, pay_charges=2 (CAPTURED×2), a2a_tasks=4 (no state column = single source),
  free orders have no deal and no charge, custodian_book has a currency column, event stream covers 12 classes across the full lifecycle.
- **Self-reported metrics and observation terminal state**: after one heartbeat cycle bob ok=5/ttft 304ms, carol ok=4/301ms, erin ok=1/305ms;
  consumer observations 3+3 all survive (6 lost 3 before the fix).
- **No lock conflicts, no 5xx**: the platform log contains only 1 expected 402 (the x402 challenge).

## Remaining backlog (P2; none affects current correctness)

| # | Problem | Location | Suggestion |
|---|------|------|------|
| A | `dispatch()` returns `{kind:"none"}` for an unknown settlement method and it is accepted silently | `gateway/settle.py` | When SETTLERS has no match, label it honestly as "not booked" or raise an alert |
| B | `record_charge/capture/fail_charge` call `conn().commit()` themselves, with no commit handover parameter | `gateway/settle.py:135,149,160` | The current call chain is safe; if it is ever wrapped in a transaction, `commit=False` must be added first |
| C | Observations can be spammed when no task is bound (accepted in phase one) | `registry.observe` | Make task_id binding mandatory in production |
| D | `probe_inbound` SSRF hardening (DNS rebinding) | `reachability.py` | Re-check the IP after resolution + ban redirects + outbound proxy |
| E | `verify-token` has no rate limit (the direct node's ticket verification interface) | `routers/transport.py` | Add a simple token bucket |
| F | Policy and registries are process-global and drift across workers | three registries | Acceptable for single-process deployment; persist centrally before going multi-worker |
| G | In the consumer observation window, `samples` takes max when rtt and total sample counts differ | `registry.observe` | Label by each list's length in the display layer, or split into two samples |

## Conclusion

- **The architecture can move to the next phase**: with the three threads (table ownership / event transactions / bookkeeping closure) closed,
  the promise "does not lie even when it crashes" now has code-level backing — money and terminal state live and die together, events and facts live and die together,
  and calls and bookkeeping live and die together (except free ones, and free is decided by the server).
- **This round's lesson goes into the process**: all tests green ≠ the system is correct. Items 11 and 12 were both only visible by "running it and looking at the database",
  so every future close-out should keep this section — "wipe and cold start + smoke + second round + live-database check" — as a fixed step before release.
- **Suggested for next round**: order the remaining P2 as A→C→D; item C (observation binding) touches "the credibility of consumer data"
  and is directly related to discovery-page ranking quality, so it deserves priority.
