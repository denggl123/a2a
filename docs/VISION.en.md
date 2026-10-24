<!-- source: 8d37c6fb9ee1 -->
# A2N highest vision

> **This document is A2N's supreme programme. Where any architecture, module, interface, pricing or business decision conflicts with it, this document wins.**
> Where this document conflicts with another document, **§1 the original intent** wins — that is the reason this project exists.
>
> Where each layer lands: what the product looks like → `docs/PRODUCT.en.md`; how the architecture is layered → `docs/design/`;
> self-sovereign mode (no server / no custody / no points) → `docs/SOVEREIGN.en.md`;
> the coordination layer (public discovery by all, level-by-level search, satisfied means stop, channel optimisation) → `docs/COORDINATION-RULES.en.md`;
> the optimisation surface → `docs/OPTIMIZATION-PLAN.md`; the gap between this document and the code → §8 / §10 here.

- **Version**: v1 · 2026-09-16 (v1.1 added 2026-09-29: §1.2 "reputation over evidence, a sample is a track record", §6.4 execution boundaries; v1.2 added the same day: §1.3 "the coordination layer and public discovery by all"; v1.3 added 2026-10-06: the §6.4 ruling that "a sample must be public" + batches B0–B6; this round's review further made it non-switchable, so delisting or unmounting does not hide historical samples. The status tables below should be read according to their own recording dates)
- **Dictated by**: the project originator
- **Recorded and organised by**: the assistant (summarising and mapping to current state only, without changing the intent)

---

## 0. In one sentence

**Turn the world's idle computers and tuned workflows into finished capabilities anyone can use — nobody needs to trust anyone, and nobody needs anyone's approval.**

Three subtitles, one for each kind of person:

| For whom | In one sentence |
|---|---|
| People with a good agent or a good machine | What you have working, others can use directly; your computer takes orders, delivers and keeps accounts by itself. |
| People whose computer cannot keep up or whose own agent is not good enough | You do not need to tinker — say what you want and someone established will do it for you. |
| Two people left with only two computers and no platform at all | You can still work for each other, keep accounts for each other and verify for each other. |

**It is not an AI API market.** An API sells one inference, and the matter ends when it is delivered;
A2N sells **one accepted, attributable delivery**. That difference runs through the entire design (§2).

---

## 1. The original intent (original text kept)

> The two passages below are the originator's original statements, **not polished and not summarised**; they are the highest basis for this document.
> Every later chapter is only an unfolding of them.

**First passage**

> I want to grow this product so that everyone in the world can use it: people with a good agent or idle resources put them out to earn,
> and people whose computer cannot keep up or whose self-made agent is too poor can very conveniently use someone else's agent.
> The biggest difference from an AI API is that it provides a finished product.
> I had also thought of a sample agent that can be deployed to your own computer with one click to earn
> (this can share revenue with the creator, but that can be developed later).
> Because of network, network latency and account differences, the agents each person discovers differ somewhat from everyone else's
> (it is not discovering every agent in the world; good enough overall is fine).
> This is my original intent, and I think it is very valuable (not about earning for myself).

**Second passage**

> Earning from idle resources, and how it differs from selling compute elsewhere: for example, someone can run h3 to make videos,
> has the whole workflow built and packages it as an agent. Then many people with low-spec computers can use someone else's agent directly,
> without tinkering themselves.
>
> On bootstrapping: first, some freebie hunters can take it for free (on the global network even unlimited, but they are also other people's testers); second, one can turn the valuable agent one has built,
> or good hardware, into a server that provides a finished agent
> (any agent must serve for free before it can charge, and rejoining the network after a disconnection also requires serving free a few times first.
> To ensure a degree of stability and reliability).

### 1.1 Broken into nine claims (wording may change, meaning may not)

| # | Claim | Chapter here |
|---|---|---|
| 1 | Usable by everyone in the world | §0 / §9 |
| 2 | A good agent or idle resources can both be put out to earn | §4 |
| 3 | When your computer or your own agent is not good enough, using someone else's must be easy | §2 / §4 |
| 4 | The biggest difference from an AI API: **it provides a finished product** | §2 / §6.1 |
| 5 | Sample agents: one-click deployment to your own computer to earn, with revenue shared to the creator (**developed later**) | §4.3 / §9 |
| 6 | What each person discovers is not exactly the same; **no full catalogue is pursued, good enough overall is fine** | §3 |
| 7 | **Not about earning for oneself** | §6.2 |
| 8 | The competitor of idle resources is not "selling compute" but "packaging a tuned workflow into a finished product" | §2 |
| 9 | Two cold-start paths: freebie hunting (the hunters are testers) + turning oneself into a server of finished agents; **free service must come before charging, and rejoining the network restarts the count** | §5 |

### 1.2 Additional dictation (2026-09-29): reputation over evidence, a sample is a track record

> **Original text kept** (the originator's wording this time):
>
> This is an agent supply-and-demand marketplace (agents are virtual, one more run barely matters, and a single transaction may be allowed to lose a little).
> I have another vision, **reputation over evidence** (both buyer and seller will have feedback and rating algorithms; malicious or dishonest trading
> is inevitably weeded out), **evidence kept light** (present, but absolute fairness is not pursued). An agent is shown by, first, its description,
> and second, its cases (the first 10 free calls **will certainly** become the agent's samples).

Broken into four claims (wording may change, meaning may not):

| # | Claim | Chapter here |
|---|---|---|
| 10 | **Reputation over evidence**: both buyer and seller have feedback and ratings; a malicious or dishonest party **gradually loses opportunities** (weeded out) rather than being killed by a single verdict | §6.4 / §10 |
| 11 | **Evidence kept light**: keep the necessary call facts, disturb people less — there is evidence, but **absolute fairness is not pursued**; this is not licence for forgery or double charging | §6.4 |
| 12 | **A sample is a track record**: an agent is shown by ① description ② cases; **the first 10 free calls certainly become samples** (what is published is the redacted, publishable projection) | §5.1 / §6.4 |
| 13 | **A single transaction may lose a little**: supply is a replaceable virtual good, one transaction's gain or loss is limited, and both sides bear the risk within explicit limits | §6.4 |

Two direct corollaries (derived, not the originator's words):

- **Claim 13 is "risk is bearable", not "errors are tolerable"**. What it relaxes is the **transaction outcome** (one botched job is not fatal),
  not **implementation quality**: double charging, faking success, tampering with history and leaking user input are still all forbidden (§6.1 / §6.4).
- **Claim 10 does not conflict with §6.1's "never synthesise a total score"**: reputation may **change opportunities** (ranking, acceptance conditions, quota),
  but must not be synthesised into a "personality total score" presented as a conclusion — it is still **evidence, not a verdict**, only applied to **choice**.

> For the operational layer (batched delivery, acceptance criteria, current blockers) see [`docs/DEVELOPMENT-ROADMAP.md`](DEVELOPMENT-ROADMAP.en.md);
> it is subordinate to this document, and where they conflict this document wins.

### 1.3 Additional dictation (2026-09-29): the coordination layer and public discovery by all

> The originator's words: on discovery, I want to force every node to be a public service; I think node a can discover node b, and if b contains nodes c, d, e,
> then a can discover level by level (each discovery optimises the shortest-path channel between the two points), until it has discovered enough that it considers itself satisfied (if not satisfied it may keep discovering).
>
> Further confirmation: yes, but this discovery is a layer between the network and the business (the coordination layer); please write detailed rules.

From this, the new product direction:

1. **The coordination layer sits between the network and the business**, organising node cooperation, level-by-level discovery, candidate preparation, pause and resume, and usable channel selection.
2. **Every online node on the network shares a bounded public discovery duty.** Pausing one's own supply still allows participation; internal nodes take part through a connection they establish themselves or a coordination relay.
3. The caller judges satisfaction by its own business conditions; satisfied it may stop, unsatisfied it may continue within an explicit budget, and no network-wide catalogue is pursued.
4. Channel information is accumulated and verified during discovery, optimising the currently known usable paths. The implementation must distinguish referral levels from actual transport hops, cannot promise that every discovery improves anything, and cannot claim to hold the network's shortest path.
5. The business layer continues to decide price, samples, reputation, order acceptance, acceptance and settlement; coordination participation cannot be exchanged for goods ranking or directly change business reputation.

The detailed design and suggested parameters are in [the coordination layer rules](COORDINATION-RULES.en.md). This section updates the old target of "public discovery is opt-in".

**Implementation status (checked 2026-10-06)**: basic public discovery, neighbour referral and the samples of the first 10 free deliveries **cannot be switched off**;
receiving `discovery:false` or `samples:false` returns 400 directly and the whole configuration does not take effect.
Optional are three independent bounded services: **hash witnessing / sealed task relay / quota-limited encrypted file forwarding**,
each with its own switch and quota in the console. The old master switch only controls witnessing and task relay.
Item-by-item implementation is in [the vision implementation record](VISION-IMPLEMENTATION.en.md).

---

## 2. What we actually sell: a finished product, not compute, not an API

This is the most easily misunderstood and most valuable point in the whole vision.

**Your example says it clearly**: someone can run h3 to make videos, **gets the entire workflow working**, and then packages it as an agent.
So people with low-spec computers can use someone else's agent directly, **without tinkering themselves**.

What they sell is **not graphics-card time**, but **"a working capability I can use directly"**.
Graphics-card time is only the raw material inside that capability — and raw material is the cheapest, most replaceable thing in any market.

| | What is sold | What the user buys | Outcome |
|---|---|---|---|
| **AI API** | One inference (tokens) | One output | Whether the output is good or usable is not the platform's concern |
| **Compute market** | Machine hours / GPU seconds | A period of time | The user still has to set up the environment and tune the workflow — **the "tinkering" is not removed** |
| **Agent frameworks / app stores** | Something installable | A program still to be configured | The responsibility for configuration and tuning stays with the user |
| **A2N** | **One accepted delivery** | **A result + a verifiable credential** | Bad work can be rejected and unpaid, and the account still holds |

This one point yields three hard constraints already in the design:

1. **The billing unit is "an accepted task", not an API call** (the business value assessment in `docs/design/`, §1.2).
   No acceptance, no charge, refunded in full — this is A2N's only real pricing power.
2. **Compute details are not exposed outward** (capability is a black box: no GPU / vram, only `deployment.region` remains;
   `gpu_seconds` is an internal billing basis only). Because you are not selling raw material, and mentioning raw material only drags the user back into price comparison.
3. **Selling compute is a losing game, selling a finished product is irreplaceable**: anyone can sell compute, and nobody beats the cloud vendors' spot market;
   but "one trustworthy delivery" cannot be given away free by anyone.

---

## 3. "Good enough is fine": discovery is a **projection of the feasible set**, not a full index

> Original intent claim 6: *because of network, network latency and account differences, the agents each person discovers differ somewhat from everyone else's
> (it is not discovering every agent in the world; good enough overall is fine).*

This is not a compromise, **it is the design goal**. The list on each person's screen is the same world projected under that person's own constraints:

| Constraint creating the difference | Mechanism |
|---|---|
| Where they are, how fast their network is | The three latency bases (platform-probed `rtt_ms` / server self-reported `ttft` / self-reported `sla`), window of 20 |
| What their account can pay with | `a2n_account.compatible_with` — the **only** implementation of the payment-capability intersection |
| Whether the other side is real | Card self-proof, three states `signed` / `unattested` / `invalid` + `tampered` |
| Whether the other side is steady | Trial progress, acceptance pass rate, public cases |
| Whether the other side can take work right now | Reachability determination (NAT / direct / tunnel / polling) + a check before dispatching |

Three disciplines that follow:

- **No global directory** (one of the three invariants of self-sovereign mode). Registration only permits being discovered; discovery is on demand.
- **Do not build "the biggest market"**. A full index sounds like the vision, but in practice it puts remote, low-spec and newly listed nodes
  permanently at the back — which directly violates original intent claims 2 and 3.
- **"Good enough overall" is an acceptable goal**: for any single search, if the person's location, account and
  network conditions can assemble what they need, this market holds. **It is not required to hold for the whole world.**

---

## 4. Where supply comes from: packaging a workflow into a finished product

### 4.1 Two kinds of supplier

| | What they have | What they do | What they get |
|---|---|---|---|
| **Craftsperson** | One working workflow / one good agent | Set up a stall: state "what is sold, at what price", everything else has defaults | **All of it is theirs** (the network takes no commission; `DEFAULT_SPLIT = {"node": 1.0}`) |
| **Person with a machine** | A good graphics card / an always-on computer | Install an agent on it (**no craft needed**) | All of it is theirs |

**These two kinds of people are exactly the two halves of original intent claim 2**: a good agent (craftsperson) and idle resources (person with a machine).
What the platform must do is not build capability for them, but **make capability directly deliverable, acceptable and chargeable**.

### 4.2 The barrier to joining: zero-cost joining, setting up a stall is optional depth

- Between "download" and "my computer is working for me" there **must be no second decision that has to be made** (PRODUCT §③).
- **Setting up a stall is inherently a creative act** (what to sell, at what price) and cannot be zero-cost — so it must be
  **optional depth, not an entry threshold**. Putting "complete your profile / configure your flow" on the first screen is exactly how you scare the idler away.

### 4.3 Sample agents (one-click deployment + creator revenue share): do it later, and not because it is low value

It lacks something that does not exist yet: **a code security model**.

- One-click deployment = one-click execution of a stranger's code;
- Card self-proof solves "**whose is this**" (identity), **it does not solve "will it rummage through my files"** (security);
- Doing it properly needs: a sandbox, permission declarations, signed distribution.

**What can be done first**: let it exist as "a manifest + instructions" (installed by following them),
rather than "one-click execution". That keeps the seed of this path without introducing an unmodelled risk.

---

## 5. Cold start: two engines (neither is "pulling demand")

### 5.1 Engine one: free quota = trading "test traffic" for "being trusted"

> Original intent: *some freebie hunters can take it for free (on the global network even unlimited, but they are also other people's testers).*

**"Unlimited freebie hunting" is an emergent property of the network, not a benefit the platform hands out.** The demand side can always find free things because three kinds of free supply
always exist in the network at the same time:

1. Every newly listed agent carries a **pre-graduation quota** (to charge, serve first) —
   and **there is no exit on the card**: declaring `x-a2n.trial=false` is **rejected outright** by listing validation
   (not silently ignored: ignoring it would let the supplier think it opted out and only discover it when billed, which is deception);
2. A node that reconnects **regains a small free quota**, used to **sample network stability parameters** —
   cases from that stretch **do not enter the quality template** (see §5.4);
3. Someone is **voluntarily free forever** (the "North China · public-benefit OCR" tier in the demo is one such case).

**"Freebie hunting" is discovered by third-party users themselves, not advertised by us.** The platform never says "free forever",
but it never stops others from saying it either — when "many agents here can be tried for free" spreads by itself among users,
that is exactly what a cold start should grow into. **What spreads is a fact, not a promise**; the difference between the two shows up on the day the first batch of agents graduates and charges:
one is "the trial ended", the other is "I was betrayed".

**The hunter is not freeloading.** What they give up is: the real call distribution, completed and failed samples, ratings, public cases —
**exactly the evidence a supplier needs to graduate**. Therefore:

> **The price of free is "being trusted"; the price of hunting is "being a tester".**

Neither end loses, because **"counting" and "evidence" are calculated separately**:

- The quota is **not counted per person but per completion** — whoever calls, it counts (a provider that completes 10 jobs but cannot graduate because "there are not 10 different people"
  feels exactly like being freeloaded on);
- The cost is that "calling yourself" also counts, and that cost is **not removed by adding restrictions** but by moving it
  **out of public evidence** (`self_source` does not enter public cases or rating statistics);
- But moving it out of statistics **is not the same as pretending it did not happen**: the interface shows, side by side, "**the mean raw score given by oneself**" and
  "**the mean raw score given by independent users**" plus **the difference** between them (no difference when samples are insufficient).
  This is **giving the difference, not a verdict** — the network does not decide for anyone "this is score inflation", it only says "how far apart these two numbers are,
  and how many samples each has", and the user uses that to estimate quality. Self-calling is an individual choice; once there are enough users in the network,
  it is diluted naturally, so it need not be banned (`a2n_reputation.self_vs_independent`).

**The line that must hold (otherwise it backfires)**:

> The network **never says "free forever"**. Free = an agent's quota that expires on graduation, or a supplier's voluntary public benefit.
> Once the former is described as a benefit, it breeds the expectation that "this product is free" —
> and then the first batch of agents that graduate and charge makes users feel **betrayed**, not "the trial ended".

### 5.2 Engine two: supply-side self-conversion (the originator becomes the first supplier)

> Original intent: *one can turn the valuable agent one has built, or good hardware, into a server that provides a finished agent.*

Three conversions, each heavier than the last:

| Conversion | Prerequisite | Result |
|---|---|---|
| A working workflow → package it as an agent and list it | Can set up a stall | Craftsperson |
| Good hardware → install someone else's (or a sample) agent | Can install | Node |
| Both | —— | **"a server providing a finished agent"** |

**Why this is the right first step**: supply does not need to wait for demand. And the originator **is the strongest supply sample themselves** —
the four demo tiers in the repository (differing in region / price book / settlement method / latency / trial state)
are the first runnable version of this path.

### 5.3 Why this is not "doing both sides" (relation to the business value assessment, §5.2)

That assessment says "do not do both sides; pulling both ends at once is suicide" — **there is no contradiction here, because the two engines are not on opposite sides**:

- **Engine one is not pulling "demand", it is pulling "test traffic".** What it serves is the **supply side's graduation requirement**,
  not GMV. The hunter appears as a "user" but actually performs the function of a "tester".
- **Engine two pulls supply**, started by the originator themselves.
- So both engines push on **the same side**, and there is no death loop of "having to pull both ends up at the same time".

**Metrics** (following that document's conclusion, unchanged): not GMV, but

- **First-search hit rate** → decides demand-side retention;
- **Acceptance pass rate** → decides supply-side trust.

### 5.4 The boundedness of the free period (otherwise "restart means free")

| Case | Quota | These cases | Code |
|---|---|---|---|
| First install `INITIAL` | **10 completions** | Enter the quality template (they are the graduation evidence) | `INITIAL_CAP = 10` |
| Reconnect `RECONNECT` | Reset to **5** (not another 10) | **Do not enter the quality template** (used to sample network stability parameters) | `RECONNECT_CAP = 5` |
| Top-up frequency per identity | At most once per calendar day | —— | `REGRANT_MIN_HOURS = 24` |
| Lifetime cumulative grant | ≤ 30 | —— | `TOTAL_GRANT_CAP = 30` |
| When charging is allowed | **Only after graduation** | —— | `graduate()` |

**Why the two quotas must be recorded separately** (`trial_offers.grant_kind` → `tasks.trial_kind`):
the 10 at first install exist to **buy graduation evidence**; the 5 at reconnect exist to **sample network stability parameters**.
Treating the latter as quality evidence is judging how well it delivers by its behaviour while "the node just came online and the network is not steady yet".
The number of excluded samples is still reported outwardly (`stability_calls` / `stability_samples` / `case_counts`) —
**isolation is not concealment**.

**The reconnect top-up is a weak constraint**: it does not detect "whether you really disconnected", and only relies on once per day + a cumulative ≤30 to contain abuse.
The reason is that the real cost of "randomly disconnecting to farm quota" is that it **hurts usage**, and an over-heavy gate hurts usage just as much — better to leave this small gap
than pretend it can be checked.

**Only "completed" calls consume quota** (failed, timed out and returned ones do not count) —
a failure must not eat the quota, since that would make the provider pay for a link problem (`consume()` is only called on the acceptance-passed path).

---

## 6. Integrity iron rules (the "things you may not say" at the vision level)

Three of them, more important than any feature.

### 6.1 The network cannot manufacture maturity, only make it visible

- **What can be delivered**: making maturity verifiable, and immaturity impossible to hide.
- **What may not be said**: *we provide finished products*, *reviewed*, *officially recommended*.
- **Where it lands** (already in the code): give evidence, not a verdict — several sections kept separate, **never synthesised into a total score**;
  graduate with real cases after 10 trials; ratings anchored on **"that rater's average across everyone"** (same-source excluded);
  disputes can be arbitrated; measurement error does not affect the "pass or not" decision.

> Original intent claim 4, "provide finished products", must be translated into outward language this way:
> **it is not that we guarantee it is mature, but that maturity cannot hide here and immaturity cannot survive here.**

### 6.2 Not a counterparty (the correct reading of "not earning for oneself")

"Not about earning for oneself" is the right stance, but it must be written executably:

- The neutral layer **takes no spread, holds no stock, and does not preferentially sell its own**;
- **Notarisation and settlement also charge nothing** (decided 2026-09-18): the network is **purely for public benefit** — however much a call settles
  is exactly what the node receives (`DEFAULT_SPLIT = {"node": 1.0}`). A custodian's role is **exchange and clearing**
  (converting different currencies into points so that accounts that do not interoperate can reconcile), it is **not a charging party**;
- The line that cannot be crossed: **once a role can both decide whether you are seen and charge for it, the middleman is back**
  (PRODUCT §1.6). So public facilities such as "rendezvous and addressing" **must not be a business**.

> "Should we charge a fee" **has already been argued out, and the answer is no** — and it does not rest on a promise but on the ledger:
> nobody has taken a cent out of your calls, and you can work that out for yourself at any time.

### 6.3 "Earning" must be honestly defined

| Scale | What the user gets | What the product calls it |
|---|---|---|
| Two computers (same Wi-Fi) | No money, still useful: work, accounts and verification for each other | **exchange** |
| Small network | A book equivalent: who owes whom how much, provable by credentials and transferable | **debt claim** |
| Large network | Real demand arrives with money, money only through the user's own channel, the product holds nothing | **income** |

**"Your computer is earning" must never appear while the account is actually a string of platform-issued points.**

### 6.4 Reputation over evidence, evidence kept light (the execution boundaries of §1.2)

This section has exactly one purpose: **do not read claims 10 and 11 as "fewer facts need recording" or "people can be given personality scores".**

**"Evidence kept light" lightens the burden, not the facts**:

| What is lightened | What must not be lightened |
|---|---|
| Making users progressively hand-sign materials, upload proofs and fill in authorisation forms | Keeping the necessary call facts (who, when, which version, what result, whether free) |
| Piling signatures, credentials and network diagnostics onto the first screen | Connection countersigning, tamper resistance and traceability at the delivery boundary |
| Complex guarantees and strong arbitration chasing "absolute fairness" | The ban on double charging, faking success, tampering with history and leaking user input |

**"Reputation over evidence" may change opportunities, never facts**:

- **Allowed**: both buyer and seller have feedback; reputation affects **ranking, acceptance conditions, quota and exploration opportunity**; persistently poor performance
  gradually reduces opportunity, and good performance can recover; a single bad review does not directly ban someone network-wide (distinguish "quality disagreement" from "protocol forgery").
- **Not allowed**: synthesising a "personality total score" as a conclusion (§6.1); counting self-source calls as independent reputation (§5.1);
  counting "technical failure / network jitter" as "poor quality"; **giving a bad review to a buyer merely for "using only the free quota"** —
  the free quota exists for testers (§5.1), so deducting for it punishes the rule itself.
- **Cold start must leave a door open**: a new agent with no history = **insufficient information**, not a bad service; it must get real exposure opportunities.

**The three hard constraints of "a sample is a track record"** (§1.2 claim 12):

1. **It must be stated before the call**: this delivery **will certainly be published as a sample** — the rule cannot appear only after the call.
2. **Certainly public, with no exempt tier** (ruled 2026-10-06): the first 10 free deliveries and "certainly public" are two sides of one thing —
   since these 10 are not charged for, they are the track record bought with real deliveries. **There is no gate of "do you agree to publish"**:
   having one would be allowing "these 10 are given away and leave nothing behind", turning free into wasted work and breaking the track record chain.
   What is still required is **redaction**: what is published is the publishable projection, with keys and privacy removed; a public preview of a media delivery is produced only by
   the node's own bounded image encoder, and the original file and fetch handle (`asset_id` / owner did) do not enter the public surface.
3. **A sample is anchored to a specific call and version**: the seller cannot "delete the unflattering ones and add a good-looking one";
   a version change must show a visible difference, and an old sample must not be passed off as a new version's performance. A placeholder sample still occupies a slot —
   that delivery really happened; if there is nothing publishable, say so, and do not use "privacy" as a sieve for picking good reviews.

---

## 7. What we do not do (the vision-level boundaries)

| Never do | Why it tempts us |
|---|---|
| **Sell compute** (machine hours / GPU seconds) | Easy to explain, easy to meter, easy to fund — but it is a price war with cloud vendors, which is lost; and it hands the "tinkering" back to the user |
| **Pursue a full catalogue / the biggest market** | "A global agent index" sounds like the vision, but it keeps remote and low-spec nodes permanently at the back |
| **Platform custody of funds** | Custody is the easiest place to take a fee |
| **Selling ranking** (recommendation slots, pinning) | The most natural, most profitable and fastest way to turn "no middleman" into a lie |
| **Describing free as a benefit** | The moment you say it, "graduation" becomes "betrayal" |
| **Calling "unsigned" money "earned"** | The numbers look good, and a beautiful growth curve is possible on day one |
| **One-click execution of a stranger's code** (before a security model exists) | It is the most seductive form of "zero barrier" |

---

## 8. Where this intent has already landed (status comparison)

> **Status note, 2026-10-05 (after the platform retirement)**: the `a2n-reputation`,
> `a2n_registry.trial`, `a2n-acceptance/dispute`, `a2n_settlement`, `gate.resolve`,
> `mint`/`burn` entries in the "code landing point" column below, and "platform-mode custody settlement / arbitration tickets", all belong to the **retired old central platform**
> (18 packages, see [RETIRED-PLATFORM.md](RETIRED-PLATFORM.en.md)), whose code has been deleted.
> The corresponding self-sovereign landing points are now: `a2n_sdk.trials` (trials / samples), `a2n_sdk.disputes`
> (the local "I do not accept it" record), and `a2n_node`'s bilaterally signed receipts with `LocalStore`.
> **§1's original intent and every judgement are unchanged; only the landing-point pointers are updated to the current code.**

| Original intent | Code landing point | Status |
|---|---|---|
| No full catalogue, good enough is fine | `a2n-p2p` gossip + on-demand multi-hop queries; registration only "permits being discovered" | ✅ |
| Each person sees different agents | `gate.resolve` (the single gatekeeping source) + `compatible_with` (payment intersection) + card self-proof + the three latency bases | ✅ |
| Peace of mind = maturity | `a2n-acceptance` (two-way metering, deviation not synthesised into a total score) + trial graduation + `a2n-reputation` (anchored ratings) | ✅ |
| Idle resources can earn | `a2n-node` already completes discovery, calls and bilateral mutual evidence between reachable nodes; platform mode may use custody settlement, and `pull` long polling supports home broadband taking orders | ⚠️ General decentralised automatic settlement not done |
| Money must not go astray | Single-table idempotent settlement (`settlements`) + reconciliation anchored to CNY only + **no issuance path on the network side** (`mint`/`burn` only accept a licensed party's deposit/payout callback) + total ≡ custody | ✅ |
| A delivery is attributable | Card self-proof + metering countersigned at the forwarding boundary + notarial stamp + dispute arbitration (`a2n-acceptance/dispute`) | ✅ |
| **Free service before charging** | `a2n_registry.trial`: 10 at first install / charging only after graduation; **the exit on the card is closed** (`validate_card` rejects `x-a2n.trial=false` outright) | ✅ |
| **Rejoining after a disconnection must also serve free a few times** | `grant()`: the reconnect top-up resets to 5 (once per day, cumulative ≤ 30); cases with `grant_kind=RECONNECT` **do not enter the quality template** | ✅ |
| **The hunter is also a tester** | Quota counted per "completion" (not per person) + self-source calls **moved out of public evidence**; and a **difference parameter** between "self-source vs independent" is given (difference, not verdict) | ✅ |
| **Packaging a workflow into a finished product** | The four demo tiers (differing in region / price book / settlement / latency / trial) = the first version of this path | ✅ demo level |
| **Reputation over evidence: both sides give feedback, malicious or dishonest parties gradually lose opportunities** | The existing `a2n-reputation` is **one-way and anchored on ratings**; **two-way feedback and "reputation changes opportunity" have not landed** | ⏳ not done (§1.2 claim 10 / §6.4) |
| **Evidence kept light: keep facts, disturb people less, no absolute fairness** | One-way metering + notarial stamp + two dispute lines (local "I do not accept it" / platform ruling) are in place; **the "light burden" defaults and bulk trace retention are not done** | ⏳ partial (§6.4) |
| **A sample is a track record: the first 10 free deliveries must be published as samples** | Single-node business implementation (`a2n_sdk.trials.TrialBook` + completion hook + `/v1/trials` + console); the buyer only reads `GET /public/v1/samples?service_id=…`, paged, with a count cap and summaries first. Neither the new nor the old switch can turn samples off; delisting and unmounting keep the public track record that already happened; old samples are projected by the current redaction rules when read, with the original record and fingerprint unchanged. The former `a2n_registry.trial` retired with the central platform. | ✅ code landed; deployment state in this round's review record (§6.4) |
| **A single transaction may lose a little** | No code landing point (this is a **risk posture**, not a feature): both sides bear it within explicit limits, with no pursuit of absolute fairness | ✅ basis (§6.4) |
| One-click deployment to someone else's computer to earn (sample agents) | —— | ⛔ missing a code security model |
| First vertical / outward commitments / income ethics | See §9 | ⏳ not done |

### 8.1 Local node wiring snapshot (2026-09-23)

This round wired the previously scattered low-level capabilities into the same long-running node, but "wired in" does not mean "the public-network transport problem is
solved":

- A remote A2A non-terminal task is polled with `tasks/get` within one overall timeout, retaining the remote task/context, status description
  and `input-required` / `auth-required`; HTTP 408/5xx is only recorded as "delivery unknown". After a timeout, a standard
  `tasks/get` only queries the original remote task and does not resend, and on completion re-enters acceptance/settlement; when a remote id exists,
  `tasks/cancel` is forwarded strictly along the path that created the task, and "cancelled" is only written once the peer explicitly confirms;
- When two long-running nodes recognise each other's signed projection, the request binds the specific supply, the full A2A input and the caller's DID;
  `tasks/get` / `tasks/cancel` must also be signed by the original caller for the specific task and operation, so a bystander cannot query or cancel using only the task number.
  An ordinary third-party A2A stays compatible but is not passed off as a bilateral credential;
- A locally queued task can be cancelled with confirmation; an arbitrary Python execution body cannot be safely killed, so shutdown only waits a limited grace period and then
  persists `INTERRUPTED` without automatic replay, and the daemon worker thread must not hold node process exit hostage forever;
- Accounts, supply mounts, consumer projections and platform publications all have explicit delete/delist paths; pausing a published supply immediately disables it locally
  and withdraws the P2P broadcast, then hides the platform entry and stops the tunnel, and resuming reuses the original agent id, avoiding the fake completion of "the interface says
  stopped but outside it is still running";
- After importing an Agent, TCP reachability and connection time are sampled in the background; the P2P control plane samples signed UDP RTT to handshaken neighbours,
  showing sent and received packets and abnormal drop counts, all keeping the most recent 30 points. The signal is only for human observation: it does not call the Agent,
  does not take part in acceptance and does not trigger settlement;
- The local call network layer maintains ordered path candidates and the most recent 30 routing records, degrading safely only for paths confirmed as not connected;
  later task queries and cancellations pin the original path. An explicitly optional voluntary sealed relay is wired in: the supply node takes work outbound
  and the public node only carries an end-to-end encrypted envelope; UDP hole punching coordinated through active neighbours is also implemented (three kinds of signed message,
  with bounded rate limiting; cone NAT usually works, symmetric NAT does not, and cross-real-public-NAT has not been measured) —
  which is still not full ICE/QUIC path negotiation;
- The long-running node is wired to a LAN beacon, independently retried bootstrap nodes and signed gossip. Queries are accepted only from handshaken
  direct neighbours; a multi-hop QUERY may return leads, but a full Card is still fetched only from a direct neighbour's measured source address, and imported only after checking the DID,
  signature, card hash, skill and entry locally;
- A node broadcasts its own supply only when the operator explicitly declares a reachable `--p2p-public-base`. Without it, it only discovers and
  does not broadcast; an exactly matching same-machine reverse proxy can read the public Card and forward A2A calls, but management interfaces still require
  local pairing, and a URL declaration by itself is not proof of public reachability.

What is still missing: **full ICE/QUIC path negotiation, and general decentralised automatic settlement of real funds**.
UDP hole punching covers only cone NAT (and has not been measured across real public NAT); symmetric NAT and CGNAT still rely on
a voluntary sealed relay to provide another real path, but that needs at least one node that is publicly reachable and has separately ticked
"provide sealed task relay" on the account page (basic public coordination discovery does not need this switch and cannot be turned off);
it is not a direct connection that "relies on no reachable third party at all". Platform custody settlement cannot prove that self-sovereign mode no longer needs a clearing role;
self-sovereign mode already has direct connections to reachable addresses, an optional sealed relay and free/bilateral mutual evidence, but it is not yet a network that can automatically complete fund transactions across
arbitrary NATs and arbitrary currencies.

---

## 9. Phases

### 9.1 What already stands (the v1 package skeleton)

The platform vertical slice is strung together: **discovery**, **calling** (the governance chain `POST /v1/invoke`), **acceptance**
(two-way metering), **custody settlement** (single-table idempotency + daily cut) and **credentials** (card self-proof + notarial stamp).
Self-sovereign mode can already complete platform-free discovery, direct calls and bilateral mutual evidence when addresses are mutually reachable; it does not include the
public-network traversal and general decentralised settlement listed in the previous section.

### 9.2 Next: the three gaps to honouring the vision

1. **A first vertical** — narrow enough that "the first 20 agents fill up the words 'good enough'",
   narrow enough to explain "why you should come here first to hang your agent".
2. **One outward commitment that can be delivered** — written per §6.1, not as "we provide finished products".
3. **A written income ethic** — per §6.2, turning "not a counterparty" into executable criteria.

### 9.3 Long term

**One-click deployment of sample agents + creator revenue sharing** (original intent claim 5).
The prerequisite is not a market but **a code security model**: sandbox / permission declarations / signed distribution.

---

## 10. Acceptance: how this vision counts as holding

Five observable behaviours, ignoring the feature list:

1. **Two people, two computers with network connectivity, and no platform at all**: they can discover and call each other, and the credentials in each hand reconcile.
   (Already demonstrable: `python scripts/sovereign_demo.py`)
2. **A user with a low-spec computer**: installing no local model and tuning no parameters, they get a result **that could not have been produced on their own machine**,
   and can verify that the other side signed at the delivery boundary.
3. **A newly listed agent**: after 10 free services, its detail page shows **real cases and ratings**, and only then does it receive its first payment.
4. **A node offline for a month**: on rejoining it **serves free 5 times first**, rather than resuming charges immediately.
5. **Nobody can buy a better position**: nobody can pay to have their agent ranked higher.
6. **For a newly listed agent, the "cases" on its detail page are the real results of its first 10 free calls**:
   it was declared before the calls that "this delivery becomes a public sample by default"; the sample is anchored to **a specific call and version**,
   the seller cannot delete the unflattering ones and cannot pass off an old sample as a new version (§1.2 claim 12 / §6.4).
7. **A persistently malicious or dishonest buyer/seller experiences "fewer opportunities", not "being killed by one verdict"**:
   reputation changes ranking, acceptance conditions and quota, and **can be revised**; a single bad review does not directly ban someone network-wide;
   and **nobody is given a "personality total score" as a conclusion** (§1.2 claim 10 / §6.1 / §6.4).
8. **When a transaction is botched, both sides bear it within explicit limits, without triggering a chain-wide halt or strong arbitration**:
   what is relaxed is the **transaction outcome** (virtual goods, one more run barely matters), **not implementation quality** —
   double charging, faking success, tampering with history and leaking input are still all forbidden (§1.2 claim 13 / §6.4).

---

## Appendix: five things already decided (2026-09-16 / 09-18)

The previous version of this document listed four open items "where the intent and the plan diverge". The originator has ruled on each; below are the decisions themselves
and their code landing points — **this section is authoritative**; where it conflicts with the body, the body changes. Item 5 was added on 2026-09-18.

| # | Decision | Why decided this way | Code landing point |
|---|---|---|---|
| 1 | **A card may not declare an opt-out from the free period**. Any agent that wants to be discovered is simply not billed for its first 10 completed calls | The purpose is not a subsidy but the **consumer**: anyone can first look at real cases to see whether this is what they want. Leaving an exit = letting the first users think they can try for free and then be charged — that is betrayal | `trial.policy_for` only reads the deployment switch; `validate_card` **rejects** `x-a2n.trial=false` outright; `tests/test_quality.py::test_card_cannot_opt_out_of_the_free_period` |
| 2 | **The purpose of the reconnect top-up is "coming back online to sample network stability parameters"**, and these cases **do not enter the quality template**; it is also a **weak constraint** | A node's network is not steady right after reconnecting, so judging "how well it delivers" by its behaviour then is wrong. But adding a heavy gate against farming is also wrong — the real cost of "randomly disconnecting" is that it hurts usage, and an over-heavy gate hurts usage just as much | `grant_kind=RECONNECT` → `tasks.trial_kind` → `ratings.stability`; filtered in four places (`quality_facts` / `counts` / `summary` / `case_list`), **and** the excluded count is reported at each |
| 3 | **"Freebie hunting" is discovered and spontaneously promoted by third-party users**, not advertised by the platform; the network never promises free forever | What spreads must be a **fact** ("many agents here can be tried for free"), not a **promise**. The difference only becomes visible on the day the first batch graduates and charges: one is "the trial ended", the other is "I was betrayed" | The basis is nailed down in §5.1; outward copy must not contain "free forever" (§7) |
| 4 | **Calling yourself during the free period is allowed**, and a **quality difference parameter** between "self-source vs independent" is given, so people can estimate quality themselves | Self-calling is an individual choice, and making it hard is pointless; once there are enough users in the network it is diluted naturally. What must be done is **lay the difference out**, not ban it — give the difference, not a verdict | `a2n_reputation.self_vs_independent`; the `self_source` block in `/v1/agents/{id}/evidence` and in list summaries; the difference badge on the console evidence page |
| 5 | **The network takes no fee at all (purely for public benefit)**: however much a call settles is exactly what the node receives; **even if there is a custodian later, it does not charge** — a custodian only does **exchange and clearing** from "multiple currencies → points", solving non-interoperability between accounts | The entry point for a fee is the entry point for a middleman (§6.2). Only by not charging can we dare to say "no middlemen"; only then does it deserve "anyone can join" | `DEFAULT_SPLIT = {"node": 1.0}`; `split_rule` version `2026.09.18`; `a2n_settlement.settle`'s runtime guard blocks any non-node share **before touching accounts**; the old 90/2/3/5 is kept only for `split_amount` replaying historical orders; `tests/test_public_good.py` |

> Decision 1 has one direct corollary worth stating separately: **"already open for business" cannot be obtained by a line of declaration on the card**.
> So the three paid tiers in the demo (East China · paid / North China · free / Singapore · x402) were changed to reach the graduated state at startup via
> `scripts/seed_established.py` **through the real semantics** (exhaust the free quota, then set graduated). It is a command-line seeding script, not any HTTP route — **there is no such back door on the product surface**.

---

*This document is a programme, not a feature specification. When judging any requirement, return to the nine claims in §1; when the nine conflict, return to the original words of the intent.*
