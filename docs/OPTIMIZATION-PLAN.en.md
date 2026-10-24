<!-- source: 8daecad61eaa -->
<!-- source: PENDING -->
# A2N overall optimisation plan

Covering the six requests (network-layer discovery / service-layer calls / automatic settlement / hard-metric evaluation / trials and cases / score normalisation), plus the two constraints added this round (**the first 10 times are not counted per person**, and **adding a "deviation from template" quality dimension**), and the console redesign requirement of "clear modules, simple and obvious".

Each item gives the **criterion** first, then the **design**, then the **acceptance**. Any design that fails the criterion is not built.

---

## 0. In one page: four work fronts

| Work front | Contains | Current state | Priority |
|---|---|---|---|
| **W1 Evidence front** (quality facts) | Template deviation (hard) + normalised scoring (soft) + public cases + genuinely signed metering | Measurement exists, **the evaluation system does not** | **P0** |
| **W2 Economic front** | Trial → graduation → charging, automatic settlement, scheduled reconciliation, isolation of statistical rubrics | Settlement is "settled in passing", no scheduled reconciliation, no notion of a trial | **P0** (trial) / **P1** (settlement) |
| **W3 Trust front** | The discovery card self-proof gate, closing off relay, replacing `mock-signature` with a real signature | None of the three is closed off | **P0** (real signature) / **P2** (the rest) |
| **W4 Presentation front** | Modular console + the "four sections of evidence" on the Agent detail page | The three entries are in place, missing a home for the new modules | **P0** |

**Why this order**: requests 4, 5 and 6 all point at the same thing — **whether the consumer side can genuinely evaluate quality**. Until that is done, "free in exchange for cases" and "normalised scoring" have nowhere to be shown, so W1 and W4 must be done in the same batch. The real signature (W3) is the evidential foundation of W1 and must be done together with it; otherwise metering reports are "mock" and the hard metrics are just a self-declaration.

---

## 1. Five unified criteria (every design passes this gate first)

1. **Every outward-facing number must state who measured it**: platform probe / server self-report / consumer measurement / counterparty credential. A number without a stated rubric is not allowed on the interface.
2. **Hard metrics are published directly, not normalised; soft scores are normalised before publication; the two are presented separately and never combined into one total score.**
   A combined total = stirring "the platform probed 200ms" together with "someone in a good mood gave 90", and the number becomes unexplainable, therefore unverifiable.
3. **Recomputable**: for any outward-facing number, a third party with the same material must be able to compute the same result. A number that cannot be recomputed is not allowed outward.
4. **One fact, one writer** (following the existing discipline).
5. **For any new threshold, first think about same-source pumping**: can someone open a throwaway identity and pump it full? A threshold that can be pumped equals no threshold.

---

## 2. W1 Evidence front

### 2.1 Template deviation (hard metric · the main measure of quality)

**What it is**: each capability declares an **acceptance template** when it is listed (what a delivery of this job has to look like to count as acceptable), and the deviation between the deliverable and the template is its quality score.

**The template has four parts** (declared at listing, publicly visible):
1. **Required structure**: fields/sections/file list, one point recorded for each missing item
2. **Required content items**: for example "must contain the original text and the translation", "must give the citation source"
3. **Quantifiable reference** (fill it in if there is one, otherwise mark "no reference")
4. **Tolerance**: how much deviation is allowed

**The deviation has three recomputable components**:

```
d_structure = missing or extra items ÷ template items
d_complete  = share of required content items not covered
d_content   = consistency distance when a reference exists (0 when there is none, and explicitly marked "no reference")

total deviation D = w1·d_structure + w2·d_complete + w3·d_content     ← weights versioned with the template and public
quality score     = 100 × (1 − D)
```

**Three iron rules**:
1. **The template and weights are declared at listing, publicly visible, and a change must bump the version**; historical cases are recomputed with **the template version of that time** (the same discipline as the existing "settlement rules are versioned" — it must be replayable two years later).
2. **The deviation must be recomputable**: anyone with the template plus the deliverable can compute the same number — otherwise it is not a hard metric.
3. **The template is a "requirement", not "the answer"**: it measures only "whether the requirements you declared were met", not "whether the result is good".
   **So it and the subjective score cannot substitute for each other, and even less can they be combined into one total score.**

**A capability with no template**: produces no deviation metric, and the interface shows "no acceptance template declared". **Faking a 0 deviation is absolutely not allowed.**

**Relation to the existing implementation**: the existing acceptance policy (`BaselineSamplePolicy` at `a2n_acceptance/service.py:16`) already returns a `score`, but with only two levels (1.0 / 0.4). Template deviation is naturally **a second policy in the same slot** (a replaceable "decision rule"), and upgrading `score` to a continuous value is enough without changing the caller.

### 2.2 Normalised scoring (soft metric)

**Formula** (the two-segment polyline reverse-engineered from the three numbers you gave; with μ=80, 40→25 / 80→50 / 90→75 all come out right):

```
μ_used = the shrunk mean of the rater
f(x) = x · 50/μ_used                          (x ≤ μ_used)
f(x) = 50 + (x − μ_used) · 50/(100 − μ_used)     (x > μ_used)
```

**Six things that must be pinned down**:

| # | Rule | Why |
|---|---|---|
| 1 | The anchor μ is **this rater's average across all suppliers**, not "the average for this one supplier" | With the latter, this supplier is forever pulled back to 50 and can never rise however good it gets = a life sentence |
| 2 | Shrinkage: `μ_used = (n·μ_raw + k·50)/(n + k)`, suggested k=10 | On the first rating μ equals that single score (output is always 50, zero information); when only one supplier has been rated, μ is that supplier's score. With few samples it must be pulled toward 50 |
| 3 | **Cap the slope at both ends** (suggested clamp to [0.5, 3.0]) | As it stands, at μ=80 the upper slope is 2.5 and the lower 0.625, so **a lenient rater can push people higher** (for the same "10 points above expectation": a lenient rater gives 75, a strict one only 58), which is counter-intuitive; at μ=90 the upper slope is 5, almost a bonus weapon |
| 4 | Boundary protection: μ=0 explodes, μ=100 divides by zero | Truncation is mandatory |
| 5 | A rating **must be bound to one specific delivery** (which order), and cannot be given out of thin air | Then every rating can be opened to show that delivery, and it naturally resists pumping |
| 6 | **The normalised score itself must be recomputable**: publish `μ_used` and the sample count n alongside the score | Otherwise normalisation is a black box decided by the platform — a direct violation of the core promise |

**Minimum sample gates (corresponding to "the first 20 times")**: both gates are recommended, with a clear division of labour:

- **The rater's gate**: until that rater has **20** ratings of their own, their ratings go only into the **local calibration pool** and are not published outward;
- **The supplier's gate**: until it has **20 deduplicated raters**, the detail page **shows cases only, no score**.

These two gates line up exactly with rule 5: **cases first, scores later.**
(One more thing that must be done: an outward score must **state both "sample count" and "deduplicated rater count"** —
"88 from 3 people" and "88 from 300 people" have completely different credibility, and looking the same is deception.)

### 2.3 Cases: the "CV" of the trial period

A new agent has no history, so **cases replace scores**. The raw material produced during the first 10 free calls is published on the detail page: who called, an input/output summary, the duration, whether the other side signed off, the template deviation, and the score (if any).

**Three rules**:
1. **Redaction and informed consent**: the user must be told explicitly **before** the call — "this one is free, and the price is that this interaction will be shown as a public case
   (the content can be redacted, and you may choose not to upload it)". **Subsidising on one hand while quietly disclosing user data on the other is not allowed.**
2. **Same-source is not published**: the calls where someone calls themselves **still count toward the 10 free places** (respecting the provider, see §3.1),
   **but they do not enter the public cases or the scoring statistics**. These two things must be separated — otherwise "10 free calls" either gets destroyed by the perception of being freeloaded on, or gets pumped away by throwaway identities.
3. **A case is a projection of existing facts, not a second truth**: the material comes from the existing tasks, metering, credential chains and process timeline
   (the existing read-only endpoint `GET /v1/console/calls/{id}` already returns the timeline), and **no extra "case table" is added**.

### 2.4 Presentation discipline: three sections, no total score

The Agent detail page has three fixed sections, **each with its own rubric and sample count, never added together**:

```
① Objective performance   platform probe rtt · consumer-measured duration · completion/return counts · metering reconciliation difference
② Quality deviation       template deviation rate (structure / completeness / content broken out) + template version + "no reference" label
③ Usage evaluation        normalised score + deduplicated rater count + the μ at the time (recomputable)
④ Cases                   the 10 from the trial period (marked "trial") + the latest N, each one openable
```

Ranking/filtering **may use any one of these dimensions**, but no "composite score" is offered. This looks counter-intuitive, but it is exactly what distinguishes the product from a "rating site": **we do not give a conclusion, we give evidence.**

---

## 3. W2 Economic front

### 3.1 Trial and graduation: first 10 free → graduate → charge

**I accept your constraint**: the quota is **not counted per person**, it is counted per call.
You also stated the reason clearly — **a provider who does 10 jobs and gets not a penny, and is then told the calls must come "from different people", feels freeloaded on**.
So: **any completed call counts**, whoever made it.

The cost is that "calling yourself" can also fill up the 10. That cost **is not eliminated by adding a restriction, but by moving it out of the public evidence** (§2.3 rule 2).
Then the provider does not feel freeloaded on, and the consumer side is not fooled by self-pumped cases — **neither side loses, because "counting" and "evidence" are computed separately rather than by making life hard for one side.**

**Counting rules**:
- Only **completed calls** count (failures, timeouts and returns do not) — a failure must not eat the quota.
- **10** on first install; **5 more** after a node reconnects.
- **The reconnect top-up must be bounded** (otherwise "restart equals free" means unlimited free):
  the same agent identity gets **at most 1 top-up per calendar day**; **the lifetime cumulative free count has a cap** (suggested 30, to be decided).
  "5 more" means **the quota resets to 5 after a reconnect**, not another 10.

**Graduation criteria (all four must hold; "parameters complete, samples reasonable" lands exactly on these four)**:

1. The free quota is used up (cumulative completions ≥ 10)
2. **Listing parameters are complete**: pricing dimensions, unit price, SLA, acceptance policy/template, settlement method — missing one item forbids graduation
3. **Enough public evidence**: ≥ 3 **non-self-source** cases (with a template deviation or a score) — this is what "reasonable samples" means
4. No unresolved dispute and no undelivered unsigned delivery

**Charging is allowed only after graduation.** This is an iron rule, and it also closes another hole:
**with incomplete parameters it is impossible to produce an order that "has a price but cannot compute the money".**

**State**: `On trial (3/10 · free)` → `Graduated (may charge)`. For an agent that has not graduated, the interface **shows no price**, only "on trial · free". The price appears only after graduation.

**Optional stall-holder promotion** (after graduation): "first order free for each new user" — enabled voluntarily by the stall holder at their own expense,
**not imposed by the platform** (because imposing it pushes the "freeloaded on" feeling right back onto the stall holder).

**Isolation of statistical rubrics (skip this and the data becomes false immediately)**: a free order naturally lands in the "non-points completion" state,
which **inflates** the success rate on the market page (while GMV counts points only and is unaffected), so the result is
**"an inflated success rate with GMV unmoved"**. So a trial order must carry a `trial` mark,
be excluded from the "paid success rate" and the GMV rubric, or **shown as two separate numbers side by side**.

### 3.2 Automatic settlement

**Current state (verified)**: settlement is done synchronously inside "the call submission", and only in points mode;
the system **does not subscribe to an "acceptance passed" event**, has **no scheduled reconciliation and no background jobs at all**.
The accurate description is "acceptance passed → settled in the same transaction", **not "automatic settlement"**.

**Target**:
1. **Event-driven**: settle automatically as soon as acceptance passes, no longer depending on "someone initiating that call".
2. **One trigger point for both bookkeeping paths**: points and direct payment/peer accounts each have their own path, and they must be closed off at **the same trigger point**.
3. **Idempotent**: the task number is the idempotency key — a repeated event must not settle twice (this is the most likely accident).
4. **Scheduled reconciliation**: reconcile once at the daily cut (total points vs custodial balance), with the difference written to the ops page and alerted on.
5. **Failures visible**: a settlement failure must enter the "pending" queue and be shown explicitly, **never silently**.

**Keep the existing virtue**: money, bookkeeping and the terminal state must be in the same transaction (that is how it is done now, do not break it).

**Final wording (implemented, the code is authoritative)**: each of the five lands in one concrete place, and changing any one of them requires changing the document, the code and the tests together.

| # | Where it lands | Key trade-off |
|---|---|---|
| 1–2 | Both `a2n-gateway.call.invoke` and `a2n-task.service.submit` call `a2n_settlement.closing.record` | **The same trigger point**: the two bookkeeping paths no longer each write their own ledger; who settled it and why it was not settled has exactly one place of truth to look at |
| 3 | `closing.record` (`task_id` is the primary key of `settlements`) | A repeated event returns `already=True` as it is — **no second count, and no after-the-fact amount change** |
| 4 | `closing.daily_cut` + `app._start_closing_loop` (`A2N_CLOSING_INTERVAL_SEC`, **default 0 = off**) | The daily cut is **not switched on automatically** by default: if it is not configured, do not quietly run it; `reconciliations` is `UNIQUE` per day, so a same-day rerun **overwrites** rather than appends |
| 5 | `closing.mark_pending` + `GET /v1/ops/settlement/pending` | A failure records one PENDING fact (**never silent**); a settled order **must not be downgraded** into pending (that would conjure an extra discrepancy out of nowhere) |

One **exception** must be stated explicitly: a bare relay deal **does not enter** this table — it has no `task_id`, hence no idempotency key, and forcing a key onto it only produces untraceable books. Relay books still follow deals' own line.

Amount rubric: `amount_minor` uses the integer smallest unit and the currency follows the money; aggregates are **never summed across currencies**,
so the three headline numbers are **counts** (no currency), with amounts broken out per currency (`closing.by_currency`).

### 3.3 New facts needed (conceptual, no implementation)

Acceptance template and version · template deviation record · rating record (bound to the task number) · the rater's calibration statistics (mean and sample count) ·
trial quota and graduation state · the signature on the metering report.

---

## 4. W3 Trust front

### 4.1 The discovery "card self-proof gate" (P2)

**Current state**: there are two paths that do not share a source — peer discovery (multi-hop queries by capability) and platform directory queries.
**The platform path dispatches straight after discovery, without the "fetch the counterparty's card → verify the card → check the hash" step**;
the self-hosted mode does have this gate. Which means the platform path is "trust by self-declaration".

**The fix**: move those four steps from self-hosted mode into the platform path, and **a card that fails verification must not appear as "directly callable"**.
Discovery and availability must be the same thing — "it is in your list" equals "it has been verified".

**Final wording (implemented, the code is authoritative)**: the gate sits on **the server side**, and only the server gives the verdict (the frontend does not guess):
`a2n_p2p.attest.verify_selfproof` (signature domain = the whole card minus `sig`) + `a2n_registry.service.card_verdict`,
and the three states must be honest: `signed` (verified) / `unattested` (no self-proof — not hidden, but **not passed off as verified**) / `invalid`+`tampered` (rejected).
`register/update_card` uses `_guard_selfproof` to block cards that "claim an identity but fail verification", and **a self-signed card must carry its own uid**
(the platform does not fill in a uid — filling one in would mean changing the signature domain on the node's behalf, and the signature would fail on the spot).
The discovery path `a2n_dispatch.query` and `assignable` skip rejected cards throughout; "directly callable" must satisfy "can pay" **and** "has self-proved" **at the same time**.

### 4.2 Closing off relay (P2)

**Current state**: there is another relay entry that bypasses the governance chain (no gate check, bookkeeping against peer accounts only).
**The fix, pick one**: ① pull it into the governance chain; ② declare it explicitly as "a low-level primitive for peer accounts only", **invisible to the consumer side**.
② is recommended, and it should disappear from the interface completely.

**Final wording (implemented, the code is authoritative)**: ② was chosen. `/v1/relay` does not appear on the consumer-side interface — the console only calls `POST /v1/invoke`
(`tests/test_relay_closure.py` scans the documents and asserts it, preventing "a second call entry quietly growing"); the 403 message points people straight back to `/v1/invoke`.
On the SDK side `call_agent` goes through the governance chain, while `relay()` remains the bare relay primitive.

### 4.3 Genuinely signed metering (P0)

**The current state is a real defect**: the signature field on the metering report is a hard-coded fake value (`mock-signature`).
So the real meaning of "verifiable" right now is only **a comparison of platform self-observation against the node's self-report**, not recomputable evidence.

**The fix**: the node signs "the metering content + the task number" with its own key, and metering that fails verification goes into "disputed" rather than "reconciled".
**And the verification result must be shown together with the detail** — "trustworthy" that gives only the conclusion and not the process is fake.

**Final wording (implemented, the code is authoritative)**: the single source of the rubric is `a2n_p2p.attest.sign_metering` (signing `task_id + node_id + dims`).
**Writing the algorithm is not the same as someone signing on the path** — the node's only opportunity is **at the forwarding boundary**: `hub.forward` sends down the `task_id` together with
**the same** `dims` **that are booked**, and `TunnelClient._handle_forward` co-signs in place after local execution and sends it back up with the response; a pushed delivery is signed by `Node._handle` on the usage it is about to report.
Attribution relies on the public key on the card: **a card that does not declare `x-a2n.sovereign.pub` is rejected outright** (otherwise any key could vouch for that node).
The SDK keeps zero dependencies: the signature format does not enter the SDK, and the caller injects `attest_fn(task_id, node_id, dims)`.
The console badge reads `objective.metering_attested` and shows `signed N/M` / `unsigned`.

---

## 5. W4 Console redesign: clear modules, simple and obvious

### 5.1 Six principles

1. **Keep the 3 main entries unchanged** (the learning curve depends on the number of entries, not on the number of features). Every new capability goes into **a sub-page inside an existing entry**; no new main entry.
2. **All operational pages go into one "Operations" area that is collapsed by default**: network overview, call testing, ledger and credentials, dispute arbitration, batch consensus, AP2 credentials, **settlement and reconciliation (new)**.
3. **Each sub-page answers exactly one question**, and the page title is that question.
4. **Sink the complexity**: lists give conclusions, detail goes into a drawer/detail page. A list row is never long.
5. **Every number carries its rubric and sample count** ("consumer-measured · 12 calls · median 820ms").
6. **A fetch failure must not be rendered as an empty state**: if loading fails, say "loading failed, tap refresh to retry", and **never say "you do not have any …"**.

### 5.2 Page map

| Main entry | Sub-page | The one question this page answers | Change this round |
|---|---|---|---|
| **Find an Agent** | Discover Agents | What is available, and can it be called directly | + trial badge, + evidence entry (into the detail page) |
| | To use | The candidates I favourited/built | unchanged |
| | Call records | What I called, and whether it settled | + settlement state column (settled/pending/failed) |
| **Sell an Agent** | My listings | How am I selling | **+ trial progress (3/10), + reasons blocking graduation** |
| | Calls from others | Who called mine, and did they sign off | + sign-off state, + template deviation in the detail |
| | Market information | What should I charge | + rubric labels, + trial orders separated from paid orders |
| **My account** | Account information | My money and payment methods | unchanged |
| | Statement | Where each entry came from | + trial/paid marks |
| **Operations** (collapsed by default) | Network overview / Call testing / Ledger and credentials / Dispute arbitration / Batch consensus / AP2 credentials / **Settlement and reconciliation** | One thing each | **+ Settlement and reconciliation** |

### 5.3 The core of this redesign: the Agent detail page = four sections of evidence

**This is where the whole redesign lands.** The detail page has four sections from top to bottom, in a fixed order, with separate sources:

```
┌ Header: name · capability · state badge (On trial 3/10 · free   or   Graduated · ￥0.05/call)
├ ① Objective performance   rtt (platform probe, 11 calls) · measured duration (consumer, 7 calls) · completed/returned · reconciliation difference
├ ② Quality deviation       template 12% · structure 0 / completeness 8% / content 4% · template v2 · reference: none
├ ③ Usage evaluation        72 points · from 9 users · μ=66 (click to recompute)
└ ④ Cases                   the 10 from the trial period (marked "trial") · the latest 10 — each opens to show the delivery and the sign-off
```

**When samples are insufficient, give the reason, not a blank**: for example "11 more users' ratings are needed before a score can be shown".
A blank will be read as "this one is bad", so it must say clearly that it is "not yet enough", not "not good".

**Separate the three sections with a vertical rule or grouped boxes, and do not sum them.** The interface itself is saying "these three things are not the same thing".

### 5.4 Three new interface elements (all small, but without them the whole logic is invisible)

1. **Trial progress**: the `On trial 3/10 · free` badge plus a one-line progress bar (visible inline in the my-listings row).
2. **Reasons blocking graduation**: when the graduate button is not clickable, it must say clearly "what is still missing" (missing a pricing dimension? 2 non-self-source cases short? something unsigned?).
   This item turns the four criteria of §3.1 directly into an actionable checklist.
3. **Settlement and reconciliation page**: three numbers (due today / settled / pending) plus a discrepancy detail. The discrepancy must be openable to show which order it is.

---

## 6. Phases and acceptance

| Phase | Content | What counts as done |
|---|---|---|
| **P0** | Genuinely signed metering; template deviation; normalised scoring + minimum sample gates; trial and graduation + `trial` rubric isolation; the console detail page's four sections of evidence + trial badge | A new agent goes through the whole "10 trials → graduate → charge" journey, the detail page shows the template deviations of those 10 calls and (when samples suffice) the normalised score; free orders do not pollute the paid success rate |
| **P1** | Event-driven settlement + idempotency + visible pending; scheduled reconciliation + discrepancy alerts; the console's "settlement and reconciliation" page | After acceptance passes it settles with nobody doing anything else; a deliberately induced settlement failure can be seen and traced on the page |
| **P2** | The discovery card self-proof gate; closing off relay; reorganising the operations area | Every card found on the platform path has been signature-verified; the consumer-side interface has no second call entry any more |

**The P0 acceptance must include a counter-example**: calling yourself 10 times → can graduate (because it is not counted per person),
**but those 10 do not enter the public cases or the scoring statistics**. Once this counter-example passes, the trade-offs of §2.3 and §3.1 are established.

**Progress: all three phases P0 / P1 / P2 have landed.** The mapping — P0 see §2 and §4.3; P1 see §3.2 and §5.4.3; P2 see §4.1/§4.2.
Each phase also has **guard-rail tests** (`tests/test_settlement_closing.py`, `test_card_gate.py`, `test_relay_closure.py`,
`test_quality.py`, `test_metering_wiring.py`) and **live-database verification** (`scripts/check_evidence_live.py`) watching it —
"it looks done" does not count; it has to be verifiable step by step on a live database after a clean wipe and cold start.

---

## 7. Risks and what we do not do

- **No composite total score.** Even if it looks easier to sell, it turns the verifiable into the unverifiable.
- **No "platform certification / recommended / gold medal".** Once there is a certification badge, there is a position that can be sold.
- **No "lowering the bar to fill up samples"**: when samples are insufficient, show "not yet enough", and do not top up with self-source data.
- **No "quietly degrading quality during the free period"**: the acceptance standard during the trial and the paid period must be exactly the same, otherwise the scores fall off a cliff after graduation.
- **The biggest risk is "ratings being instrumentalised"**: suppliers pumping each other, or colluding to pump the denominator.
  There are only three lines of defence: **bind to a specific delivery**, **deduplicate and publish the sample count**, **exclude same-source**.
  Weight adjustments beyond these three are futile — without the three, everything after is fake.

---

## 8. The 5 decided items (final, the code is authoritative)

They were originally listed as "awaiting your decision" and are now all finalised at the suggested values. **The authoritative home of the numbers is the code constants**; only the conclusion and the reason are recorded here.

| # | Conclusion | Why this value | Code |
|---|---|---|---|
| 1 | 10 on first install; a reconnect **resets** the quota to 5 (not another 10); at most 1 top-up per calendar day; **lifetime cumulative grant cap 30** | No cap = "restart equals free", i.e. unlimited free; a cap without counting per person is what keeps the provider from feeling freeloaded on | `a2n_registry.trial`: `INITIAL_CAP=10 / RECONNECT_CAP=5 / TOTAL_GRANT_CAP=30 / REGRANT_MIN_HOURS=24` |
| 2 | Graduation needs **≥ 3 non-self-source cases** | 0 = only talking to yourself; 1–2 = one bad review settles it. 3 is the minimum at which stability is visible, and deliberately stays a low bar (a new node should not be blocked dead by an evidence threshold) | `a2n_reputation.ratings.MIN_NON_SELF_CASES = 3` |
| 3 | The two slopes of the normalisation **clamped to [0.5, 3]**; shrinkage **k=10**; the anchor μ→50 unchanged | No z-score: z-score loses the two fixed points 0→0 / 100→100, and no longer has the explainable anchor "50 = neutral in their eyes". The clamp only suppresses the amplification at extreme μ (at μ=5, 50/μ=10, and one low score can drive a supplier to the floor) | `a2n_reputation.ratings.normalize_score` + `SLOPE_MIN/SLOPE_MAX`, `K_SHRINK=10` |
| 4 | **Both gates**: a rater's ratings are published only after 20 ratings of their own; a supplier gets an outward score only after 20 **deduplicated** raters | Merging into one gate loses half the meaning: the first treats "a score given by someone who just arrived is untrustworthy", the second treats "3 people pumping 100 entries is untrustworthy". The two gates treat two different diseases | `a2n_reputation.ratings.PUBLISH_MIN_RATER / PUBLISH_MIN_RATERS = 20` |
| 5 | Template weights default to **structure:completeness:content = 3:4:3**; **with no reference the content weight goes to zero and the other two are renormalised** | Completeness highest (4): "not giving what should have been given" is a quality accident; structure next (3): the shape is wrong but the content is still usable; content can only be measured with a reference, and without one it is **not measured** (not recorded as a 0 deviation); weights are archived with the template version so historical cases can be replayed | `a2n_acceptance.template.DEFAULT_WEIGHTS`, the `no_reference` branch of `deviation()` |

**The discipline of finalisation**: all five numbers are written into code constants, each with a guard-rail test (boundary value plus the reverse). Changing any one of them requires changing this document, the code and the tests together — a rubric inconsistent in three places is not decided at all.
