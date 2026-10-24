<!-- source: 5c190023d7d7 -->
# A2N consumer-side aggregation: combining several candidate agents into one stable call path

In one sentence: **I am the consumer and I want to call a capability such as "finance", but a single agent is unstable (down, timing out, misbehaving).
So I configure a set of candidates plus a policy in my own client (round robin, by weight, switch on failure, cooldown and backoff),
and use whichever is stable — this is purely consumer-side convenience and has nothing to do with the supplier.**

## Positioning (three distinctions)

1. **It registers no card and takes no platform task**: no virtual agent, no middleman ledger, no margin.
   The platform is entirely unaware of this aggregator — it is just a piece of routing logic inside the consumer's process
   (client-side load balancing, whose members are agents).
2. **Every successful call is a one-to-one transaction between "the consumer and the real agent"**:
   settlement, acceptance, stamping and observation all go through the existing chain (`call_agent` times the call and reports the observation automatically).
   The platform's accounts contain only real transactions, not a shadow of the aggregation.
3. **Candidate health is measured by the consumer (a local private ledger)**: ok/fail, RTT sliding window and cooldown state all stay
   with the consumer (the basis defined in `docs/CALL-PARAMS.md`: whoever measures reports it, and reports it to itself).
   The platform cannot see it and does not need to — "how I keep my calls stable" is the consumer's private business.

> This is a different shape from "supplier-side aggregation" (a virtual card reselling members and earning the spread). A2N phase one only does
> consumer-side aggregation: it creates no new facts and its accounts are naturally one-to-one; if resale ever has real demand, it gets its own storefront.

## Mechanism

```
Consumer process
┌────────────────────────────────────────────┐
│ Aggregator(client, candidates=[...])       │
│   call(skill) ─▶ policy picks candidate ─▶ call_agent ─┼──▶ real agent A(w5)
│                │failed? try next ─▶ call_agent ─────────┼──▶ real agent B(w1)
│                └all failed ─▶ AggregationFailed         │
└────────────────────────────────────────────┘
```

| Policy | Behaviour |
|------|------|
| `round_robin` | Round robin: the cursor starts in turn and moves on after a failure |
| `weighted` | Smooth weighted round robin (SWRR): high weights are picked first and the share is about the weight ratio, while low weights are still guaranteed a turn |
| (custom) | `register_strategy("name", fn(candidates, state) -> [attempt order])` |

**failover is the shared fallback of every policy**, not a separate policy:

- One candidate fails → try the next (default attempt count = number of candidates, configurable with `max_tries`);
- A failed candidate enters **cooldown**: duration = `cooldown_ms × min(consecutive failures, 5)` — repeated failures lengthen it,
  and it recovers automatically on expiry ("failure refreshes the state on a timer"); the next selection skips candidates in cooldown;
- **When everyone is cooling down, probe half-open**: better to try the candidate whose cooldown expires first than to give up outright;
- Everyone genuinely fails → raise `AggregationFailed` (carrying each candidate's last error) and let the caller decide its own fallback.

> Weight is the consumer's configuration (a declaration); when a high-weight candidate fails it enters cooldown and naturally falls to a lower weight —
> stability is backed by failover, not promised by weights. That is what "if the high weight is usable, use the high weight first" means.

## Candidate prerequisites and accounts

- **Candidate prerequisite**: the consumer only needs **usable payment capability** for each candidate — free, or a paired peer
  account, or a direct-payment channel already bound in that candidate's `accepts` (judged uniformly by the `call_agent` server gate,
  with the same semantics as the A2A entry). Supplying that capability is the consumer's own business; the aggregator does not do it for them.
- **Accounts**: every successful call = a one-to-one settlement between the consumer and the real agent (points, peer account or direct payment,
  through that agent's own gate and acceptance). The aggregator keeps no ledger and touches no money — the three iron rules are not even indirectly bent.

## Usage

```python
from a2n_sdk import Client
from a2n_sdk.aggregate import Aggregator

cli = Client("http://127.0.0.1:18787", principal="acct:me")
agg = Aggregator(cli, candidates=[
    {"agent_id": "ag_big",  "weight": 5},   # primary: stable and fast
    {"agent_id": "ag_mid",  "weight": 2},
    {"agent_id": "ag_back", "weight": 1},   # fallback
], strategy="weighted", cooldown_ms=30_000)

result = agg.call("fin-strategy", {"ticket": "..."})   # switches automatically on failure
print(agg.health())   # candidate health: a local private ledger (ok/fail/cooldown/mean RTT)
```

## Relation to other mechanisms

- **Observation reporting**: the duration of a successful call automatically enters the platform's "consumer" basis (`connection.observed`),
  so other consumers can see it on the discovery page — the aggregator's health data and the platform data come from the same source but carry different rights:
  the platform's copy is public market information, the local copy is my private basis for routing.
- **SLA declaration / self-reported TTFT / probed RTT**: references when picking candidates on the discovery page; only the measurement after aggregation
  is my own truth.
