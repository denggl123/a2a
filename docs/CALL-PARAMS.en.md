<!-- source: a95828f5741e -->
# A2N call parameter specification: data ownership from the consumer's point of view

In one sentence: **in an agent call the consumer cares about two kinds of thing — "whom to pick" (discovery and selection parameters) and "how this order went" (call and after-the-fact parameters).
Every parameter must have exactly one owner: declared by the server, probed and booked by the platform, or measured by the consumer. None may speak for another.**

## Iron rules

1. **Declarations belong to the server**: capability, price book, settlement method, runtime environment (data-residency region), SLA commitments — the supplier writes them in the card,
   and the platform only stores a snapshot without rewriting it (rewriting would create a second truth). Implementation details (GPU, concurrency and so on) are a black box; they do not go into the card and need not be reported.
2. **Bookkeeping belongs to the platform**: reputation, KYA, status, accounts — the platform derives them from events, and neither the node nor the consumer may write them directly.
3. **Measurements belong to whoever measured**: the server's execution experience (TTFT) can only be measured by the server SDK; the end-to-end round trip can only be measured by the consumer
   side (under P2P the platform cannot measure it). Whoever measured it reports it, and the platform only aggregates and displays.
4. **Displays must state their basis**: `declared / self-reported / probed / consumer`, and a latency number without such a label must not appear on the discovery page.
5. **Measurements must resist inflation**: when bound to a task_id the platform strictly checks both ends of the task (requester/node); unbound reports only enter the
   window (phase one), and production should make binding mandatory.

## Discovery and selection parameters (what the consumer sees on the discovery page)

| # | Parameter | Owner and basis | Where the data is | How it is collected | UI label |
|---|------|----------|---------|---------|---------|
| 1 | skills capability | Declared by the server | card (agents.card_json snapshot) | Written at listing time | (declared) |
| 2 | price_book pricing | Declared by the server | card; the algorithm explanation is in settlement.price | Written at listing time | (declared) |
| 3 | accepts settlement method | Declared by the server | card (peer_account / x402 / …) | Written at listing time | (declared) |
| 4 | deployment.region runtime environment | Declared by the server | card → agents.compute (the column name is kept for history; the value stores only `{"region": ...}`) | Written at listing time (single-point projection by `deployment_of()`, older cards' compute.region is accepted for compatibility) | (declared) |
| 5 | sla.max_latency_ms | Committed by the server | card → agents.sla | Written at listing time | SLA declaration |
| 6 | ttft_avg_ms first response | **Measured and self-reported by the server** | agents.connection.metrics | Server SDK sliding window during execution (20 samples) → reported in the heartbeat | Self-reported |
| 7 | rtt_ms connection | Probed by the platform | agents.connection.rtt_ms | Inbound probe (direct mode only) | Probed |
| 8 | observed.total_avg_ms end to end | **Measured by the consumer** | agents.connection.observed | Consumer SDK times the call → observations report back (sliding window of 20) | Consumer |
| 9 | reputation / kya / status | Booked by the platform | agents columns | Derived from events (bump_reputation and so on) | Platform |

> The meaning of the three latency bases: `SLA declaration` is the committed ceiling (used for filtering); `self-reported` is the server's recent experience;
> `probed` is platform ↔ node; `consumer` is the only real round trip "between the two SDKs". The values are not comparable to each other and cannot be converted.

## Call parameters (supplied by the consumer when making a call)

| Parameter | Owner | Notes |
|------|------|------|
| agent_id / path / body | Consumer | Whom to call, what to call |
| budget | Consumer | Budget ceiling (capped by the contract price); whether it is frozen depends on the settlement mode |
| currency | Consumer | Must match the paired terms and price book currency (no currency switch on closing) |
| token (call credential) | Issued by the platform | Short-lived credential; the server checks the pairing relation |
| timeout | Consumer | Implied in phase one (relay 15s); by the specification it belongs to the consumer |

## After-the-fact parameters (what each side gets once the call completes)

| Parameter | Owner | Flow |
|------|------|------|
| usage metering | Submitted by the consumer (reported on the node's behalf) | tasks table, recomputed at acceptance |
| amount / amount_minor | Booked by the platform | Computed by amount_of (the contract price), carried through the four-party tables |
| result_hash | Stamped by the platform | notary credential chain |
| ok / total_ms observation | **Measured by the consumer** | Reported back to observations automatically (best effort) |
| ttft_ms per call | **Measured by the server** | Enters the server sliding window and is self-reported with the next heartbeat |
| X-A2N-Billing (relay billing) | Booked by the platform | A relay response **header** (the body stays protocol-pure A2A); a free agent has no such header; a paid agent triggers peer bookkeeping |

## Data landing points at a glance (who writes which table or field)

```
Server SDK (node) ──heartbeat──▶ agents.connection.metrics   (self-reported TTFT)
                                 agents.connection.mode/nat  (connection declaration)
Server SDK (node) ──listing────▶ agents.card_json/compute/sla (declaration, backed by the card hash; the compute column stores only region)
Platform probe/bookkeeping ────▶ agents.connection.rtt_ms     (probed; a heartbeat does not overwrite it)
                                 agents.reputation/kya/status (derived from events)
Consumer SDK      ──report─────▶ agents.connection.observed   (end-to-end measurement, bound to the task to resist inflation)
Consumer SDK      ──call───────▶ tasks/ledger/deals…          (through the gate and acceptance; never written directly)
```

## Interfaces

- `POST /v1/registry/agents/{id}/observations` (X-Principal = the consumer)
  body: `{task_id?, rtt_ms?, total_ms?, ok?}`; when bound to a task, both the requester and node ends are checked.
- The heartbeat `POST /v1/registry/agents/{id}/heartbeat` gains a `metrics` field (self-reported by the server).
- SDK: `call_agent()` / `relay()` time and report automatically (`call_agent` also binds task_id); an explicit `observe()` may carry a task_id.

## Addresses and bypassing: a card is not a way around A2N

**Conclusion: what can be bypassed is "connecting to the node directly"; what cannot be bypassed is "the gate, metering, settlement and stamping".**

| Scenario | Can the node be reached directly | Consequence |
|------|------------|------|
| pull / wss / relay / tunnel | **Physically no** — the node has no public entry, and the address in the card is local or internal | Only the platform relay is possible |
| direct (the node reports its own public address) | If the address exists it may be connected directly | The platform cannot guard it for the node; **the node must verify credentials itself** |

The platform-side lines of defence (already landed):

1. **Outwardly always publish only the A2N door number** `/v1/relay/{agent_id}` —
   `GET /v1/registry/agents`, `GET /v1/registry/agents/{id}` and discovery
   follow the same rule; `card.url` / `connection.url` / `card_url` are all projected,
   and the real address is given only to the owner (needed to fill in the listing). **After projection, a direct connection still hits the platform entry**,
   so the gate, metering, reconciliation and stamping cannot be bypassed.
2. **card_hash is untouched**: it backs the original card, not the projected card.
3. **The node guards its own door (zero trust)**: a direct node can be reached directly on the public internet, so the platform can only "answer what is genuine
   and make no decision" — on receiving `X-A2N-Call` the node calls `POST /v1/transport/verify-token`
   to ask the platform, and returns 401 if verification fails. On the SDK side `serve_local_agent(..., verify=platform_verifier(...))`
   wires it up in one line; if `bind` opens to the public internet, `verify` must be supplied at the same time, otherwise the capability is simply given away.

What a bypass call means (a bad trade for the consumer):
- **No credential**: the platform did not stamp it, so this call does not exist on the credential chain;
- **No acceptance**: nobody judges whether the result is right, and in a dispute A2N has nothing to rely on;
- **No account**: the supplier receives no money and can still claim from the consumer afterwards (bilateral bookkeeping and direct payment each have their own evidence).
