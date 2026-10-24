<!-- source: 28ca6e996afc -->
> **Current status, 2026-10-05**: the business has been unified into a single node platform, and the old central platform code and demo assembly are retired. A full SDK installation has 5 packages. For current features, business gaps and actual acceptance, see [README](../README.en.md), [node unification](NODE-UNIFICATION.en.md) and [the Docker report](../artifacts/docker-acceptance.md). What follows keeps the original product thinking and phased plan; historical platform modules and past test counts are not proof of completion in this round.

# A2N product definition (alignment draft)

> Parent document → [`VISION.md`](VISION.en.md) (the highest vision). This document only answers "what the product looks like".
>
> It discusses only three things: **what the product finally looks like, the design philosophy, and the user experience**. Nothing about implementation.

---

## In one sentence (revised)

Original: *"Turn every idle computer in the world into an agent stall that takes orders, delivers and keeps accounts by itself."*

That sentence only makes **supply** clear; it does not explain **why a user dares to use it**. It should become a main sentence and a supporting one:

- **Main sentence (definition)**: every exchange happens without a middleman — **your computer is both the stall and your ledger.**
- **Supporting sentence (outward)**: install once, and your computer starts taking orders, delivering and keeping accounts by itself. **The ledger is in your hands, so nobody needs to be trusted.**

Why the change: "stall" in the original is supply-side language, while the product's irreplaceability lies in "the ledger is in your hands".
Saying the two side by side is how a user learns that what they get is not just "the ability to earn", but "not having to trust anyone".

---

# Part one: what the product finally looks like

## 1.1 It consists of two objects and one face

| | What it is | How the user describes it |
|---|---|---|
| **The stall** | What I offer, at what price, when I can take work | "I sell things here" |
| **The ledger** | What I did, who signed for it, how much settled, whether the other side accepts it | "This is my account" |
| **One face** | A line of status + one sentence-sized entry point | "What is my computer busy with / I want to order in one sentence" |

After installation, this is all the user sees, **and there is no fourth thing**.
The original document mentioned both "the ledger" and "the three identities", but "the stall" was never named as an entity in the product —
it is where the craftsperson lands, and it is the answer to "what exactly is my computer selling".

## 1.2 One face, serving three kinds of people at once

Not three products or three sets of interfaces, but **three ways of using the same home page**. The home page has only two things:

```
┌─────────────────────────────────────────────┐
│  Busy · 3 orders today · 12.5 earned        │   ← one line of status
├─────────────────────────────────────────────┤
│  Say what you want done…             [ Send ]│   ← one input box
└─────────────────────────────────────────────┘
                     (click the status → my stall / my ledger)
```

| Role | The one thing they do on this face | Time spent | What success looks like |
|---|---|---|---|
| **Idler** | Nothing; occasionally reads that status line | A few seconds | Opens it a month later and sees income |
| **Consumer** | Says one sentence in the input box | The time of one sentence | The result comes back, and they know it can be verified |
| **Craftsperson** | Clicks from the status into "my stall" and sets it up once | One-off, interruptible | Wakes up the next day to a stranger's signature |

**Why cut it this way**: the difference between the three roles is not the interface but **time spent**.
Any home page that forces an idler to make a choice shuts out the largest group.

## 1.3 The product's four stable states (more important than a "feature list")

| State | Trigger | Interface | Share |
|---|---|---|---|
| **Quiet** | Nothing to do | Nothing shown | The vast majority of the time |
| **Busy** | A task is running | The icon moves very slightly (the only animation in the whole product) | A minority |
| **Has something to say** | Something they must know: first income, a ledger that fails verification, a key that should be backed up | Lights up once, then back to quiet | Very rare |
| **Abnormal** | Credentials do not match, the counterparty reneges | **Never silent**, but said only to the person concerned, and must state "what you can check yourself" | Rare |

**Quiet is the default state, and a promise.** For a product that "disappears after installation", almost the whole interface budget goes to "has something to say" and "abnormal".

## 1.4 Interruption budget (turning "do not disturb" into an executable number)

The original document says "no pop-ups, no interruptions" but also says the user should see "the first time you are discovered" and "the first income" — these two conflict. The solution is to give interruption an allowance:

- **Within 24 hours of installation: 2 proactive appearances allowed.** One is "there are other people on the network", the other is "your computer received its first signature".
  (Basis: the interface copy never says "earn" — money without a signature must not be called income; for signed money, "received" is enough.)
- **Afterwards: only "abnormal" may appear proactively, once per matter.**
- **Never allowed**: update notices, campaign notices, operational notices, how much someone else earned.

This allowance must be nailed down. It is not restraint; it is **proof of the product's positioning**: only something that does not make money from attention is allowed not to interrupt.

## 1.5 What form it takes, and what it does not

**It is**: an icon on the desktop; clicking it opens a page on this machine.

**It is not**: not a website, not an account back office, not a subscription service, not a browser extension.

The original document wavered between "a small icon" and "opening a page", so this needs stating clearly:
**that page is local, not in the cloud.** With the network down, the source gone or the platform vanished, the page still opens, the accounts are still readable and the credentials still verify.

It also has no "my homepage", "followers", "feed", "levels" or "points store" — it does no identity performance, only exchange.

## 1.6 "No platform" has to hold up: no central power

Outwardly, stop saying "no platform" in general terms — that claim gets punctured by reality. The version that holds is:

> **No central power**: no party holds your money, your identity, your ledger, or "whether you may take part".

- There are **public facilities** such as rendezvous and addressing (like street lamps); they read public capability and address information as needed;
  they do not intervene in deals, do not read private task inputs and outputs or account balances, and do not profit from discovery or ranking.
- It must not be "that entry point we happened to open". **Once a role can both decide whether you are seen and charge for it, the middleman is back.**

This line is the easiest place in the product definition to breach oneself, and the hardest to walk back.

### 1.6.1 Node model: the same software, sharing the public discovery burden (target revised 2026-09-29)

**Every computer installs the same full software**: identity, supply, calls, mutually signed receipts, console, and the coordination layer between the network and the business layer.

Per the originator's latest confirmation, every online node must, within its resource quota, answer discovery queries, provide its public supply and referrals to related nodes, and assist access. A node with no goods of its own, or with sales paused, can still take part in public coordination. Nodes may go offline and may rate-limit explicitly when busy; not everyone is required to have a public IP, and internal nodes take part through an established outbound connection or a coordination relay.

Discovery keeps its search progress and its pace at the initiating node: A learns of C/D/E through B, verifies, deduplicates and continues as needed; it stops when satisfied, and when not satisfied it may add a round with an explicit budget. Channels are updated from actual reachability and observation. The full rules are in [COORDINATION-RULES.md](COORDINATION-RULES.en.md).

Basic discovery is enabled by joining the network online; task relay, receipt witnessing and large-file caching declare capability and quota separately. Private accounts, management interfaces and task content do not become open because of public cooperation. Network contribution cannot be exchanged for goods exposure or change business reputation.

**Current implementation (2026-10-06)**: a full SDK installation unifies 5 packages (`a2n-node` / `a2n-sdk` / `a2n-p2p` / `a2n-acceptance` / `a2n-kernel`) and assembles the product as `Daemon + NodeRuntime`; the old 18 central platform packages are retired, see [RETIRED-PLATFORM.md](RETIRED-PLATFORM.en.md). Basic public coordination and free delivery samples are always on and cannot be switched off; witnessing, task relay and encrypted file forwarding are chosen separately. Signed handshakes, paged referrals, level-by-level discovery, pause and resume, persisted quota, channel probing and the outbound coordination mailbox are implemented. See [SDK-ARCHITECTURE.md](SDK-ARCHITECTURE.en.md). Full public-network interoperation across five nodes and a real reputation pilot still need later acceptance.

There is already UDP control-plane hole punching and an explicitly selected sealed task relay; verification in a real public-network environment still needs separate treatment. A task relay does not automatically have the ability to forward coordination requests. Witnessing only accepts the necessary signed hash declarations and does not carry a global ledger, arbitration or custody settlement. The old demo platform (`a2n-server`) was retired wholesale at the 2026-10-05 close-out and **is no longer a compatibility channel**, see [RETIRED-PLATFORM.md](RETIRED-PLATFORM.en.md).

<details>
<summary>Historical note: the 2026-09-25 "public service switch" model (original text kept)</summary>

What follows keeps the original text and the implementation description of the time, to help understand the compatibility code. Its goal of "public discovery may be switched off" has been replaced by the new 2026-09-29 target above; whether network capability works still has to be judged by actual testing. **Also: the "old demo platform" (`a2n-server`) mentioned in the original text was retired at the 2026-10-05 close-out, see [RETIRED-PLATFORM.md](RETIRED-PLATFORM.en.md); every mention below refers to its form before retirement.**

> **Further note (2026-10-05)**: the original model of "one switch controls everything" has also been superseded in the implementation.
> Basic coordination discovery **cannot be switched off** (`/v1/public-services` returns 400 for `discovery:false`),
> and only samples, witnessing and task relay may be refused, as three separate switches in the console.
> Keeping a "one master switch" would compress three different degrees of willingness into one boolean,
> making people think "all off = I do not take part in public discovery" while basic discovery continues — that is lying, not simplifying.
> `public_service_enabled` is kept only as a compatibility view (read: on if any of the three is on).

> **Further revision (2026-10-06)**: the first 10 free deliveries must be published as samples, so samples are no longer an optional service; neither the new nor the old switch can turn them off. The old compatibility switch now only reads and writes witnessing and task relay. The three-switch description above only reflects the implementation of the time.

**Former title: node model: the same software, one switch (decided 2026-09-25, revised)**

There are no two species called "platform" and "node", and no division of "roles". **Every computer installs the same
full software**: identity, supply, calls, mutually signed receipts, a full-featured console, and **built-in public service
capability** (directory, routing, notarial stamping) — only the last of which is opened outward according to one switch:

> **Switch: "allow as a public service"**
> - Off (default): you can still find, sell and keep accounts yourself; you simply do not take on the public directory, routing
>   and notarial work for other nodes. Selling your own Agent still requires answering buyers' calls;
> - On: this node additionally opens its directory and notarial capability to other nodes in the network, voluntarily taking on
>   the cost of bandwidth, storage and staying online. **The full functionality stays; the switch trims nothing.**

Hence "the platform" is no longer another thing — **it is simply a node with the public service switch on**. Any computer
can become it at any time, and none is required; closing up shop is just switching it off, and the remaining nodes in the network
each stand on their own.

Implementation status: the long-running node persists this switch, and after configuring a reachable HTTP entry can provide a read-only,
bounded directory of verified Agent Cards, DID routing leads and hash witnessing of bilateral receipts. Public queries do not trigger
external fetching; both sides keep the full receipt and acknowledgement locally, and the witness node only receives both sides' signed declarations
of the same receipt hash, keeping the declaration hash and its own signature, and receives no task, capability, input, output or amount. Beyond routing leads,
the P2P layer has implemented **UDP hole punching coordinated through active neighbours** (three kinds of signed message, PUNCH_REQ/PUNCH_HINT/PUNCH,
with bounded rate limiting): cone NAT usually connects directly, **symmetric NAT does not work and a sealed relay should be used instead**; measured verification across
real public NAT is still pending. There is also a voluntary sealed relay that must be chosen explicitly: a supply node only takes work outbound, and the
public node carries an end-to-end encrypted envelope but can see node/supply identifiers, size and timing. Witnessing is not a global ledger,
arbitration or custody settlement. The old demo platform is still a compatibility channel. An ordinary node may pick several voluntary public nodes as discovery
sources, and every returned card is still verified locally; a direct Card does not pass through a public node, only a sealed relay Card does, through the chosen
node. Full ICE/QUIC path negotiation and custody-free real-money payment are still not done.

In one sentence: **installing the SDK = a full node + a full-featured console, controlling only itself; whether it is willing to
also serve everyone is this computer's own switch.**

</details>

## 1.7 "Earning" must be honestly defined (the most important open question)

The place in the product definition most likely to be glossed over, and the most fatal. It must be said in three layers, never mixed:

| Scale | What the user gets | What the product calls it |
|---|---|---|
| 2 computers (same Wi-Fi) | **No money, still useful**: work for each other, keep accounts for each other, verify for each other | "exchange" |

The reconciliation mechanism of the first layer (calls between independent self-sovereign nodes and resident A2A nodes with mutually recognised projections are implemented; an ordinary third-party A2A cannot be passed off as mutually signed): **self-sovereign settlement = bilaterally signed receipts**. When each delivery completes,
the caller and the supplier sign the same credential and each keeps a copy; the original caller must also sign to authorise later task queries and cancellations; reconciliation is checking signatures and bases pairwise —
the same `task_id` reconciling consistently means it is clear. **No custody means no issuance, so there is no ledger that the whole network must agree on**;
the very need for "there must be a general ledger" is eliminated, and this is the root reason "it can work without a server".
| Small network | A book equivalent: who owes whom how much, where the "owe" can be proven by credentials and is transferable | "debt claim" |
| Large network | Real demand arrives with money, and money only moves in and out through the user's own channel; the product holds nothing | "income" |

**"Your computer is earning" must never appear while the account is actually a string of platform-issued points.**
Either say honestly "your computer is accumulating debt claims", or make it genuinely convertible into money. This one item decides whether the product is
"an exchange network" or "a Ponzi with a story".

## 1.8 The end-state picture (completed)

The original document's end state is "a global agent labour market". Three more sentences make it decidable:

1. **Countless stallholders, but not one shelf.** The demand side states conditions and qualifying stallholders surface by themselves;
   nobody can buy a better position.
2. **No customer service, no arbitrators, no operations** — this is not cost saving, it is the product's shape:
   the rules apply to everyone consistently, so no human is needed to explain and make exceptions.
3. **As transaction volume grows, the product's interface does not grow.** A hundred million entries and one entry look the same in the ledger;
   if getting bigger means adding features, pages and operations, the product was built wrong.

---

# Part two: design philosophy (from 5 slogans to 6 criteria)

Principles are not for admiring; they are for **rejecting requirements**. Each one gives: the criterion / what it rejects / the most common mistake.

## ① Equivalent exchange, not trust

- **Criterion**: any interface element that asks the user to "choose to believe" must be replaceable with "see for yourself".
- **Rejects**: platform guarantees, official certification, verified, recommended, gold badge.
- **Most common mistake**: turning "verification" into a ceremonial green tick. A green tick must land on **that specific transaction** —
  clicking it must show "which credential, who signed it, what was signed". **Verification nobody understands is no verification.**

## ② Every computer is an auditor

- **Criterion**: the ledger's first reader is the user themselves; the product's disappearance does not change the validity of any record.
- **Rejects**: any design where "you must first connect to someone to see your own account".
- **Most common mistake**: **doing the truth but not the explanation.** The user does not want a string of check values but
  "what this was, with whom, when, how much, whether the other side accepted". The truth is for those who verify;
  the explanation is for everyone — both are needed, and the order cannot be reversed.

## ③ Onboarding cost approaches zero

- **Criterion**: between "download" and "my computer is working for me" there must be no second decision that has to be made.
- **The cut that must be made honestly**: this rule constrains **"joining the network" and "ordering in one sentence"**,
  and **does not constrain "setting up a stall"**. Setting up a stall is inherently a creative act (what to sell, at what price) and cannot be zero-cost;
  it must be **optional depth**, not an entry threshold.
- **Most common mistake**: putting optional depth on the first screen and scaring the idler away with "complete your profile".

## ④ The network should be like air

- **Criterion**: the interface shows no words like "connecting, online, offline, address, syncing" —
  unless they directly cause "my result has not arrived".
- **Rejects**: topology diagrams, network status panels, node lists as the main interface.
- **Most common mistake**: making "the first time you are discovered" a flashy star map. It excites the user for five seconds and in
  the sixth makes them think "this thing needs network configuration".
  The feeling to deliver at that moment is **"there are others"**, not **"there is a network"**:
  give "three neighbours, one of which just took a job", not a topology.

## ⑤ A small network must be useful, and a large one need not exist

- **Criterion**: there must be a complete, tellable story between two computers: work for each other, keep accounts for each other,
  verify for each other. **A feature that is meaningless with two computers is not a core feature.**
- **Rejects**: everything premised on an "ecosystem" — leaderboards, trending, curated, community, influencers.
- **Most common mistake**: using "there will be many nodes later" to justify something whose value cannot be explained today.

## ⑥ Not interrupting is a promise, not a preference (missing from the original document, but necessary)

- **Criterion**: any proactive interface must answer "what real loss does the user suffer by not dealing with it right now".
  If it cannot answer, it must not appear.
- **Rejects**: push notifications, red dots, update prompts, "your neighbour is waiting for you".
- **This one must withstand the most profitable temptation**: if "give a popular stall a recommendation slot" could significantly raise revenue,
  it still must not be done — because it is the first step of the attention business. **Not doing the attention business
  only counts as real once it has been refused at a fork where money was available.**
- **Silence is an asset**: the quieter the product, the heavier it speaks. So the one "abnormal" moment must be extremely clear.

---

# Part three: user experience

## 3.0 One arc (the six moments are not six features but one arc from doubt to ownership)

```
a silent promise → there are others → there is income → there is a result → there is confidence → there is business
   dare to install    dare to keep      dare to use        cannot leave
   ├──────── the first three solve "dare to" ────────┼──── the last three solve "cannot leave" ────┤
```

Each moment must satisfy three things at once: **what the user did (≤1 step), what they must understand, and what they feel at that moment.**
Miss one and the moment was wasted.

## 3.1 The six moments (+ three the original document lacks but the product must have)

### Moment 1 · Installation (target < 1 minute)
- **What the user does**: double-click. Then nothing.
- **The only sentence they see**:
  "Your computer has joined the network. When idle it will take work for you, and the earnings are recorded on your own computer."
- **Acceptance**: this screen **must contain no question for the user to answer** — no registration, no phone number,
  no preferences, no "do you allow".
- **Emotional target**: like plugging in a power cable, not like signing a contract.
- **Most likely failure**: after installation there is no signal that "it is working normally", so
  the user thinks it is broken and uninstalls. So what comes next must be **"status"**, not "get started".

### Moment 2 · The first time "being discovered" (within seconds)
- **What they see**: there are others. Three neighbours, one of which just completed an order.
- **Emotional target**: so it is not just me.
- **Acceptance**: what appears on this screen must be **traces of people**, not network structure.
- **Most likely failure**: showing a star map or a node list → the user thinks network configuration is needed → they quit.

### Moment 3 · The first income
- **Show only three things**: what was done, how much was earned, the credential is checkable.
- **Acceptance (the product's honesty floor)**: the amount must be immediately followed by "signed for by the other side".
  **Money without a signature must not be shown as income**; better to show "awaiting the other side's confirmation".
  Confusing "I think I earned" with "the other side accepted" zeroes the product's credibility on the spot.
- **Emotional target**: "my computer really did complete one by itself just now, and the other side signed for it."

### Moment 4 · The first "order"
- **What the user does**: says one sentence. The result comes back.
- **Acceptance**: between "one sentence" and "the result", the user **makes no choice** —
  no picking a stallholder, no picking a price, no picking a method.
- **Emotional target**: hailing a ride does not require knowing the driver.
- **One exception that must be added**: when the result is bad, the user needs an **"I do not accept this order"** action,
  and that action is itself one of the product's core capabilities (it produces a credential saying "I do not accept it").
  **"There is no arbitrator" does not mean "the user has nowhere to speak"** — the former is a refusal of a third party, the latter is a dereliction towards the user.
- **Most likely failure**: making the user "pick an agent" (pushing the decision cost back), or
  prices not being transparent (telling you what to pay only after the result returns).

### Moment 5 · The first "reconciliation" (**the most important emotional delivery in the whole product**)
- **What the user does**: opens the ledger and clicks "verify my accounts".
- **Acceptance**: the verification result must land on **a specific transaction** — clicking in shows the credential itself,
  not just a green tick.
- **Emotional target**: **from now on I do not need to trust anyone.**
- **Most likely failure**: the green tick is decoration. The user clicks once, does not understand, and never clicks again —
  and the product's core differentiation stops working.

### Moment 6 · The first "setting up a stall"
- **What the user does**: answers two questions — **what do you sell, at what price**. Everything else has defaults.
- **Acceptance**: setting up a stall must be **simple enough to finish on a phone, on the subway**.
- **Emotional target**: "I have a stall in a global market."
- **Most likely failure**: turning it into technical work (configuring flows, tuning parameters, writing prompts).
  It is not technical work, it is setting up a stall — **the stallholder decides what to offer, but the act of offering must be simple.**

### Moment 0 · Before installing (missing from the original document, but it decides installation volume)
- The download page has one sentence, and **a sample of someone else's ledger you can open and inspect** (redacted, checkable).
- **No promise of earnings, no promise of scale** — promising those two puts the product back in the "attention business".

### Moment 7 · Day 30 (the real steady state)
- The product no longer contains any "first time". **"Quiet" becomes the subject of the experience**: for a month it did not interrupt the user,
  and the ledger grew thicker by itself.
- The user's motive for opening it is **"I want to see my account"**, not the product calling them back.
- **This is this product's steady state, and the only evidence that it differs from every attention product.**
  If day 30 is bad, the first six moments being beautiful does not help.

### Moment 8 · The one moment it must interrupt
- **Credentials do not match, or the key is not backed up.**
- The product must speak, and must speak so that a non-technical person understands.
- **Silence is an asset to be spent where it counts**: precisely because it usually says nothing, this one time carries weight.

## 3.2 The experience in one sentence

**What the user always perceives is "my one sentence got a result" and "this ledger is my own".
Everything in between — other people's computers, where they are, how they were found — should never be perceived.**

---

# Part four: the negative list (strengthened)

Keep the original 6 items, add 4, and mark for each **why it tempts us** (without writing down the temptation, it cannot be resisted).

| # | Never do | Why it tempts us |
|---|---|---|
| 1 | The platform holds your funds | Custody is the easiest place to take a fee |
| 2 | Please trust that we have reviewed it | Review is the easiest thing to turn into a threshold and a charging point |
| 3 | Feed / push / red dot | It is the most effective retention tool |
| 4 | Mandatory account registration | Accounts are the easiest way to get growth data |
| 5 | Letting "onboarding" take more than one step | Every extra step buys better data |
| 6 | Features that only work with a large network | The large-network story is easier to tell and easier to fund |
| 7 | **Selling ranking** (recommendation slots, pinning, priority display) | It is the most natural, most profitable and fastest way to turn "no middleman" into a lie |
| 8 | **Calling "unsigned" money "earned"** | The numbers look good, and a beautiful growth curve is possible on day one |
| 9 | **Requiring users to understand keys / credentials / networks to use it** | Explaining it looks professional. But they must be hidden — **hiding them ≠ not letting you look**, always keep a "verify it yourself" entry |
| 10 | **Manufacturing anxiety for retention** (how much others earned, you are behind, limited time) | Anxiety is the best recall tool |

Items 7 and 10 are new and the most important: **they are the most likely ways this product dies.**

---

# Part five: how this definition counts as holding (overall acceptance)

Ignore the feature list; look only at five observable behaviours:

1. **Within 3 minutes of installation**, the user can say what their computer is doing without reading any instructions.
2. **With only two computers**, two people can work for each other and verify for each other, and neither needs to trust a third person.
3. **Any "how much was earned"** can be clicked into and shows the other side's signature.
4. **On day 30**, the user's motive for opening it is "to see my account", not the product calling them back.
5. **After everything external disappears**, the ledger in the user's hands is still complete and still counts.

If all five pass, the product is the thing it claims to be; if one fails, that one is what it really is.

---

# Part six: four open questions that need your decision

As a product definition, without these four settled every detail afterwards will wobble:

1. **Is the first meaning of "earning" cash, debt claims, or both in stages?** (see 1.7)
   Without settling this, "your computer is earning" cannot go into the product.
2. **Who bears the cost of a bad delivery?** With no third-party adjudication, the lightest answer may be:
   no signature = no payment + a "not signed for" record in the ledger + one rework allowed.
   But whether that "one" has a cap, and who counts it, must be defined.
3. **What happens if the key is lost (for non-technical users)?**
   The product-level answer is not "recover your account" but **"identity is a piece of paper you can copy by hand and lock in a drawer"**.
   Whether to design a "give it a piece of paper" experience for that piece of paper needs a decision.
4. **How much of the ledger is shown to others?** The default should be: **show only that one transaction's credential, not the whole ledger.**
   This decides what credit information the stall page can display.

---

## Appendix: what this alignment changed relative to the original document

| Original document | After alignment |
|---|---|
| The one sentence only says "stall" (supply side) | The main sentence says both "stall" and "the ledger is in your hands" |
| Has "ledger" and "identity" but no "stall" entity | Product = two objects (stall + ledger) + one face, stated explicitly |
| Says "disappears after installation" and also "must see the first income" | Introduces an **interruption budget**: 2 times in the 24 hours after installation, only abnormal afterwards |
| "Small icon" and "opens a page" used interchangeably | Explicit: desktop icon + a **local** page (not a website, not an account back office) |
| Says "no platform" | Changed to the defensible **"no central power"**, with public facilities barred from becoming a business |
| "Earning" is undefined | Three layers made clear: exchange / debt claim / income, and a debt claim must not be called income |
| Design philosophy is 5 slogans | Changed to 6 **criteria** (each with "what it rejects" and "the most common mistake"), adding "not interrupting is a promise" |
| 6 moments | Adds **moment 0 (before installing), moment 7 (day-30 steady state), moment 8 (the only necessary interruption)**, with "acceptance" and "most likely failure" for each |
| 6 items on the negative list | 10 items, each marked with **where the temptation comes from** (unwritten temptation cannot be resisted) |
| Says nothing about how success is counted | Adds "five overall acceptance points" |
