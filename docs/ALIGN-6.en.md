<!-- source: 71c2f029171d -->
# Six requirements · code alignment

Each point below was checked item by item against the existing A2N code, and points 5 and 6 also get definitions for the new mechanisms and the questions that must be settled.
Every item cites `file:line` so it can be re-checked.

---

## 1. Does the network layer support agent discovery — **yes, but via two paths that do not share a source**

| Item | Current state |
|---|---|
| Discovery by capability | ✅ `a2n_p2p/node.py:188` `query(skill)` sends a QUERY, and the responder matches on `skills` (`node.py:159`) |
| Multi-hop | ✅ QUERY `ttl=2` (`node.py:195`), forwarded when `ttl>1` (`node.py:305`) |
| Message trustworthiness | ✅ Signature + deduplication + TTL clamp (`envelope.py`) |
| Where discovery results land | ⚠️ By default only in the in-memory `PeerTable` (`peers.py:42`, skill index `peers.py:87`); only one path writes a table: `wiring.py:256` `p2p_discover_to_roster` → the `rosters` table |
| The platform registry path | ⚠️ **A separate second path**: `POST /v1/discovery/query` (`routers/registry.py:210`) → `dispatch/service.py:60` queries SQLite `skills/agents` |

**Three gaps worth pointing out**:
1. The two paths (p2p discovery vs the platform registry) **do not share a source**; their only meeting point is the `rosters` table, and only the p2p side writes into it —
   this does not conflict with the earlier discipline of "one writer per table", but **the answer to "who can be seen" depends on which path you take**,
   which is the same class of risk as "split facts".
2. **Discovery ≠ usable**: the platform path dispatches an order as soon as discovery finishes, without the step "fetch the other side's card, verify it, check the hash";
   the self-sovereign path has it (`a2n_node/node.py:196` `find()`, four steps), the platform path does not. **The platform path lacks a "card self-proof" gate.**
3. Discovery results are not persisted (on the self-sovereign side `a2n_node/node.py:192` only returns offers), so "whom did I discover yesterday" cannot be looked up anywhere.

---

## 2. Does the service layer support agent calls — **yes, but there is one bypass**

| Entry | Landing point | Goes through the governance chain |
|---|---|---|
| `POST /v1/invoke` | `routers/invoke.py:39` → `gateway/call.py:70` | ✅ |
| A2A JSON-RPC | `routers/a2a.py:81` → `_message_send` (`a2a.py:145`) → the same `invoke` | ✅ |
| relay (bare relay) | `routers/transport.py:92` | ❌ **Bypass**: only verifies `call_token` (`transport.py:115`), only books `peer_account` (`transport.py:142`), **skips the gate, no direct payment or x402** |

- The governance chain is shared: `gate.resolve` (`gate.py:85`) + `tasks.create/submit` + `settle.dispatch` (`call.py:89-136`)
- Synchronous: `hub.forward` blocks waiting for the reply, 15s timeout (`hub.py:122`)
- **No retries**; failures produce receipts (relay timeout 504 `transport.py:128`; channel failure `tasks.fail` `call.py:107`; acceptance failure REJECTED `task/service.py:271`)

**Gap**: this relay bypass is a hole in "the service layer supports calls" — it goes around the governance chain, and although my earlier note
defined it as "a bare relay primitive for peer accounts only", **it is currently reachable from the consumer side**, and it needs to be cut off cleanly (either folded into the governance chain, or made invisible to consumers).

---

## 3. Does settlement support automatic settlement — **not "automatic", but "settled in passing at submit time"**

| Item | Current state |
|---|---|
| Trigger | ⚠️ `tasks.submit` calls `settlement.settle` **synchronously**, and only when `settle=True` (points) (`task/service.py:277`) |
| Event driven | ❌ **No**. Wiring only subscribes to `acceptance.failed` / `arbitration.resolved` / `node.suspended` / `epoch.anchored` (`wiring.py:84,112,124,159`) — **it does not subscribe to `acceptance.passed`** |
| Non-points path | Dispatch books at the tail of the call chain (`call.py:135`), which is **not the same trigger point** as the points path |
| Same transaction | ✅ `settle(commit=False)` shares one `tx()` with the transition to SETTLED (`service.py:246`) — money and terminal state live and die together, which is an advantage |
| Split | ✅ Defaults node 90 / author 2 / fee 3 / pool 5, with the remainder to node (`a2n_kernel/policy.py:10,27`), and the rule is versioned and replayable |
| Reconciliation | ⚠️ `settlement/reconcile.py:23` compares total points with the custody balance, but **it can only be called manually** via `GET /v1/ops/reconcile` (`public.py:81`); **there is no scheduled task anywhere in the repository** (no scheduler/cron) |

**Conclusion**: claiming "supports automatic settlement" does not hold up at present. The accurate statement is —
**acceptance passes → settlement happens in the same transaction** (this is true, and stricter than most systems),
but **the trigger surface is incomplete, there is no event-driven automatic settlement, no scheduled reconciliation, and no background daemon**.
"Automatic" currently depends on "someone making that call".

---

## 4. Is the hard-metric evaluation system complete — **measurement exists, an evaluation system does not**

**The quality facts that exist (four kinds, from different sources)**:

| Source | What it is | Landing point |
|---|---|---|
| Platform probe | Inbound reachability / `rtt_ms` | `registry/reachability.py:101` |
| Server self-report | `wall_time_ms` / `ttft` (**visible only to the owner, stripped from the business surface**) | `sdk/runner.py:130` → `registry.py:231`, stripped at `registry.py:97` |
| Caller measurement | End-to-end round trip ("only the caller can measure end to end") | `POST /registry/agents/{id}/observations` (`registry.py:197`) |
| Acceptance policy | Two-way metering reconciliation (±10%/500ms tolerance), SLA latency, `score` used only for internal grading (1.0/0.4) | `acceptance/service.py:16-52` |

**The existing hard gate on "who may be paid" (the closest thing in the project to a hard-metric system)**:
`a2n_settlement/dims.py` — a dimension registry where **only what can be metered and verified may be billed**;
`gpu_seconds` / `cpu_seconds` / `vram_peak_gb` / `wall_time_ms` may only enter statistics and reputation, and can never become money.

**What is missing (four things)**:
1. ❌ **There is no "consumer rates supplier" action**: no rating API, no rating table — all 25 tables in the scheme were checked, there is no `rating`/`review`; a repository-wide search for `rating|review|stars` returns nothing.
2. ❌ **There is no multi-dimensional aggregate score**: `a2n_reputation` only has a scalar "event → weight adjustment" (`reputation/service.py:17-42`, in the [0,1] interval) and distinguishes neither capability nor dimension.
3. ❌ **There is no reverse reputation** (supplier rating consumer); reputation is attached only to `agents.agent_id`.
4. ❌ **There is no trace of "time-limited free" or "trial quota"**: `trial|quota|free_calls` return nothing; `free` only means "no price on the card = free" (`price.py:103`), not a promotional quota.

**One hard flaw that must be called out**: `usage_reports.signature` is always `"mock-signature"` (`task/service.py:260`) —
metering reports are **unsigned**. So the real meaning of "verifiable" right now is "comparing the platform's own observation with the node's self-report",
**not "signed, recomputable evidence"**. If hard metrics are to serve as quality facts externally, this must be fixed first.

---

## 5. A new agent's first 10 calls free (first 5 after a reconnect) + details including those 10 cases and ratings

### My understanding

This is a **two-way transaction**: for a new stallholder, the platform trades "free" for "evidence".
A new agent has no history, so the detail page **gives cases instead of a score** — the raw material of the first 10 calls
(who called, a summary of input and output, duration, whether the other side acknowledged, and the rating) is published on the detail page,
letting consumers assess quality by "reading cases instead of reading a score".

I understand "first 5 free after a reconnect" as a **quota reset**: after a network interruption or node restart, that agent's free quota
resets to a smaller number (5), so that **it can still be tried after reconnecting**, rather than "charging the moment it is back, so nobody wants to try again".

### Data basis (no new facts needed)

Everything the cases need **already exists in the current facts**: `tasks` (state/duration/amount) + `usage_reports` (metering and reconciliation)
+ `receipts` (credential chain) + `notary.for_task` (process timeline), and there is already a read-only endpoint
`GET /v1/console/calls/{id}` returning `timeline`. **A case is a projection of existing facts, not a second truth.**

### Four things that must be settled first (otherwise they are abused the moment they launch)

1. **"10 calls" counted by whom — I strongly recommend "10 different consumers, one each" rather than "any 10 calls".**
   There are two reasons, both hard: (a) counted as "any 10 calls", one large customer can eat the whole quota, so **the 10 cases come from the same person,
   which is the same as having no cases**; (b) counted as "10 different consumers", **self-inflation is blocked by construction** (opening your own alt accounts cannot fill 10 people).
2. **Reset on restart = free forever.** The reset must be constrained: limit the number of resets **per agent identity** (per key),
   limit the time window (e.g. no repeated reset within 24 hours), and set a **lifetime cumulative ceiling**.
3. **Free in exchange for public disclosure must be stated to the consumer in these words before the call.**
   "This one is free, and the price is that this interaction will be shown as a public case (content can be redacted, and you can choose not to upload it)" —
   you cannot hand out a subsidy while quietly disclosing user data. **This is a floor, not an option.**
4. **Free orders must be labelled, otherwise they pollute global statistics.**
   A free call naturally lands in `ACCEPTED` (completed without points) rather than `SETTLED`, so `a2n_market`'s
   `success_rate = done/total` (`market/service.py:45,151`) is **inflated** by free orders,
   while GMV counts only points (`market/service.py:31`) and is unaffected — the result is "success rate inflated, GMV unchanged".
   **Free orders must be tagged `trial=true` and excluded from the "paid success rate", or presented separately.**

---

## 6. Normalising consumer ratings

### First work out the formula (reverse-engineered from the three numbers you gave)

Your example: average 80 → pulled to 50; next 80 → 50; 90 → 75; 40 → 25.

**Only one two-segment polyline satisfies all three points**: the anchor maps `μ` (that rater's own average) to 50,
while keeping `0 → 0` and `100 → 100` as fixed points:

```
f(x) = x · 50/μ                    (x ≤ μ)
f(x) = 50 + (x − μ) · 50/(100 − μ)  (x > μ)
```

Substituting μ=80: f(80)=50 ✓　f(90)=50+10×2.5=75 ✓　f(40)=40×0.625=25 ✓ — **all three numbers match**,
so I take this to be the curve you want.

### What it is for (the part I agree with)

**It removes the "lenient / harsh" rater bias**: after normalisation `50 = neutral in this rater's eyes`,
so scores from different people become comparable for the first time. That is right, and it is standard practice.

### But four things must change (otherwise it backfires)

1. **The anchor must be "their average across everyone", not "their average for this one seller".**
   With the latter, this supplier **is always pulled back to 50** and can never rise however well it does — a life sentence.
   If this is not clarified, requirement 6 turns the whole evaluation system into "nobody can ever gain score".
2. **It removes "scale position" bias but amplifies "scale width" bias.**
   With μ=80 the upper slope is 2.5 and the lower 0.625: for the same "10 points above expectation", **a lenient rater gives 75 while a harsh rater gives only 58**
   — the more lenient the rater, the more they push people up, which is counter-intuitive. With μ=90 the upper slope is 5, and "a slightly high score from a lenient rater" almost becomes a bonus weapon.
   Two options: **cap the slopes at both ends** (a small change); or **subtract the mean, then divide by their own standard deviation**
   (a z-score form, which removes both position and width bias and is more correct).
3. **μ is very unstable early and also degenerates.** On the first rating μ equals that single score (output is always 50, zero information);
   with only one seller rated, μ is that seller's score. Add **shrinkage**: `μ_used = (n·μ + k·50)/(n+k)`,
   pulling μ towards 50 when samples are few. Also `μ=0` blows up and `μ=100` divides by zero, so **bounds are mandatory**.
4. **Normalisation can only correct "soft scores given by people", not "the quality of the facts".** So requirements 4 and 6 must be settled together as one discipline:

   > **Hard metrics (who measured, on what basis) go out as they are, with no normalisation; soft ratings go out normalised;
   > the two are presented separately and never merged into one total score.**

   Merging them into one total score mixes "the platform's probed 200ms" with "a 90 someone gave because they were in a good mood",
   and the resulting number is unexplainable, hence unverifiable — which violates the product's core promise.

### The "first 20 are not passed to the supplier" clause needs you to confirm which 20

It could mean two completely different numbers, with very different meanings:

- **The rater's 20**: they need ≥20 ratings to establish a stable μ (used for normalisation);
- **The 20 the supplier receives**: it needs ≥20 **deduplicated raters** before a score is published, and until then only cases are shown.

My suggestion is **both, with a clear division of labour**: until a rater reaches 20, their ratings enter only the local calibration pool and
are not published; until a supplier reaches 20 deduplicated raters, the detail page shows cases only, no score
(which dovetails with requirement 5's "a new agent uses cases instead of a score": **cases first, score later**).

### One more hole that must be guarded

A normalised score must be **published together with the sample count and the deduplicated rater count**. Otherwise "88 from 3 people" and
"88 from 300 people" look identical, while the credibility of the two is not the same thing at all.

---

## Appendix: decisions needed from you

1. Requirement 5: is the free quota counted **per agent** (10 different consumers, one each) or by number of calls?
2. Requirement 5: which constraints on the restart reset — "call limit + time window + lifetime ceiling"?
3. Requirement 5: who decides the redaction of public cases — redacted by default with the supplier able to publish, or public by default with the consumer able to opt out?
4. Requirement 6: confirm the anchor is "the rater's average across everyone"; are the slopes at both ends capped, or changed to a z-score?
5. Requirement 6: confirm "20" means two gates — "20 ratings by the rater" plus "20 deduplicated raters for the supplier";
6. Requirement 3: should settlement become **event driven** (subscribing to `acceptance.passed` to settle automatically), and reconciliation scheduled?
7. Requirement 4: when will `usage_reports.signature`'s `mock-signature` be replaced with a real signature? (Without that, hard metrics cannot serve as external evidence.)
