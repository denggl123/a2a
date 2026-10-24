<!-- source: dbae6447e226 -->
> **Implementation update, 2026-10-06**: the code, verified scope and remaining external dependencies of B0–B6 are in [the vision implementation record](VISION-IMPLEMENTATION.en.md). Earlier design drafts and the historical progress below do not represent this round's state; real payment, a fifth public node and a new-device pilot are still incomplete.

> **Current status, 2026-10-05**: the business has been unified into a single node platform, and the old central platform code and demo assembly are retired. A full SDK installation has 5 packages. For current features, business gaps and actual acceptance, see [README](../README.en.md), [node unification](NODE-UNIFICATION.en.md) and [the Docker report](../artifacts/docker-acceptance.md). What follows keeps the original product thinking and phased plan; historical platform modules and past test counts are not proof of completion in this round.

# A2N development roadmap: real samples and bilateral reputation

**Detailed design added 2026-10-06**: business rules for the undelivered parts of the vision are in [VISION-BUSINESS-DESIGN.md](VISION-BUSINESS-DESIGN.en.md), and the six-layer responsibilities, five-package modules, data and interfaces, compatibility migration and acceptance are in [VISION-ARCHITECTURE-DESIGN.md](VISION-ARCHITECTURE-DESIGN.en.md). The new B0–B6 batches refine the current gaps; it is suggested to do the fact specification, samples and cross-node experience first, then the reputation shadow run, with installation and public-network verification in parallel. Both are design drafts awaiting implementation, and suggested parameters and new protocols are not completed features.

Planning date: 2026-09-29. Code baseline: `dev / 14f1be6`.

This document is a development plan awaiting implementation, not a completion report. It follows the vision the originator made explicit in this session: a public-benefit Agent supply-and-demand marketplace that allows a small loss on a single transaction, favouring long-term reputation over heavy evidence; an Agent is shown first by its description and real cases, the first 10 free calls become samples by default, and both buyer and seller will later have feedback and rating algorithms. Where it disagrees with an older document's reading, the user's statement this time defines the goal; thresholds and policy adjustments proposed here are marked as suggestions and are not treated as decided.

**Coordination-layer route added 2026-09-29**: the originator confirmed that all online nodes carry public discovery, and that discovery belongs to the coordination layer between the network and the business. Detailed rules, the C0–C4 development batches and 20 acceptance items are in [COORDINATION-RULES.md](COORDINATION-RULES.en.md). A stable goods identity is a shared prerequisite; level-by-level discovery and the R1 public samples can proceed in parallel, and the satisfaction policy gradually integrates the feedback and reputation of R2/R3. **Coordination batch C0 (contracts and stable identity) has landed (2026-10-01)**: interfaces, stable goods key, node capability declaration, state and quota model, plus node identity signing/verification for three kinds of object, see `a2n_sdk.coordination` / `a2n_node.coord_identity`; C1–C3 and the basic C4 integration were implemented on 2026-10-05 and the public discovery switch has been split; see [SDK-ARCHITECTURE.en.md](SDK-ARCHITECTURE.en.md).

**The product experience to deliver**

A seller hangs up its own Agent and states clearly what it can do, what input it needs and what it will deliver. The first 10 free services automatically accumulate real samples; the buyer reads the description and samples and tries it, and both sides' feedback gradually influences later selection and order acceptance. Good services and honest users keep getting transaction opportunities; occasional dissatisfaction and limited bad debt have a bearable way of being handled.

A2N's transaction commission is zero, and public infrastructure sells no ranking or exposure. Light evidence means automatically keeping the necessary call facts and disturbing the user less; it is not licence for double charging, faking success, tampering with history or leaking user input. Allowing a single transaction to lose is both sides bearing a limited transaction risk and cannot become an excuse for implementation errors.

This round's delivery target is the product node driven by `a2n-node.Daemon`. Modules that already existed in the old `a2n-server` served as reuse references (that package was deleted at the 2026-10-05 close-out, see [RETIRED-PLATFORM.en.md](RETIRED-PLATFORM.en.md)), and its passing tests cannot directly mean the product node already has those capabilities.

**The starting point that has been verified**

| Capability | Current fact | What it means next |
|---|---|---|
| Local to container calls | The local 8890 can discover the container; a measured game-design old card gave connection refused, and the new card reported "this upstream has no skill None" | The request arrives, but delivery has not succeeded. Fix protocol adaptation and old mounts first |
| Protocol adaptation | `a2n_node.supply.mount_supply` always mounts as A2A; `a2n_sdk.greenlight.serve_http` accepts plain JSON | Mount according to the real upstream protocol, keeping a unified external call entry |
| Supply lifecycle | A normal case's upstream uses a random port, and the address takes part in deriving the service_id; an old mount is restored from persistence | The same goods should have a stable identifier, and a restart on a new port should not produce an extra goods |
| Trials | Rules such as 10 at first install and 5 on reconnect live mainly in the old `a2n_registry.trial` | Wire trial state into the product node's acceptance, completion and discovery paths |
| Trial end and charging | The old order-creation logic decided free by `used < cap`, while the graduation interface separately checked cases and disputes; when the quota ran out it could resume the listed price without having graduated | Unify trial state, the price quoted beforehand and charging conditions; do not copy mutually contradictory judgements |
| Cases | The old `routers/quality.py::case_list` returned hashes, metrics and scores, with no actual delivery preview | A sample's content must be something a buyer can understand |
| Bilateral feedback | The old `a2n_reputation.ratings` only had buyer rating seller | Add seller rating buyer; a failed order also needs a feedback entry suited to its state |
| Reputation | The old rules had a 20/20 publication threshold and a score accumulated from success events; the product node is not wired in yet | Reuse the statistics and deduplication ideas, and redo cold start and anti-gaming |
| Funds state | The product node defaults to `NoSettlement`, and a listed price does not mean receipt is implemented | When payment is enabled, wire in a minimal payment path and show the receipt state honestly |

Measured failure records: `check-7f7c098923f54dab8444853d75854ab8` (old upstream unreachable), `check-3c205d32511a476dbac4f259b13ce1a5` (new upstream protocol mismatch). These are test calls and cannot be used to deduct a real merchant's reputation.

**Development order and completion criteria**

| Batch | What it delivers to the user | Dependencies | Pass criteria |
|---|---|---|---|
| R0 Calls and lifecycle | Can find the current supply and actually get a result after opening it | Current code | All five normal container cases deliver successfully; deliberately failed cases return the real reason; repeated restarts add no card and change no goods identity |
| R1 Description and the first 10 samples | Can read the description, see delivery samples, try for free, with real progress | R0 | A brand-new Agent goes from 0/10 to 10/10; samples appear automatically and are viewable; restarts and retries do not double-count; a bad sample is not replaced by a good one |
| R2 Bilateral feedback | The buyer reviews the actual service, the seller reviews the cooperation; both can explain and correct | R1 | One valid feedback per side per task; repeated submissions are idempotent; an undelivered task cannot be reviewed for finished-product quality; a low score and a reasonable rejection do not automatically become malicious behaviour |
| R3 Reputation influences transactions | Buyers find suitable services more easily, sellers can control acceptance risk | R2 with real data | Scoring is explainable; self-inflation and repeated mutual rating have limited influence; newcomers get a chance; persistently poor performance lowers the probability of later selection |
| R4 Small-amount payment | After the trial, trade at the stated price, with both sides knowing whether it is paid | R1, R2; may run in parallel with R3 | One currency and one real channel complete payment; a retry does not charge twice and a failure does not fake receipt; A2N's share is zero |
| R5 Independent user trial | Real buyers and sellers keep using it, and parameters are corrected by real behaviour | R0–R4 | Several independent nodes and real workflows run continuously; selection, reuse, feedback coverage and false positives are compared |

The first deliverable version ends at R2: calls, description, the first 10 samples and bilateral feedback form a free market that can be tried. The second version wires in R3/R4 to observe whether reputation improves transaction choice. Each batch is accepted on actual behaviour before the next begins.

> **Progress (written back as batches complete)**: **R0 is done** (doc `84daf4a`; code `5988cc7`/`ac5691a`/`c96c834` — real protocol mounting, goods identity independent of address, swapping the upstream in place without resetting goods identity; host↔container measurement: 5 green cards all `completed`, the dead card honestly reporting `Connection refused`, two rounds of restarts adding no card and changing no identity). **R0 hardening (2026-09-29, five issues from the R1 review)**: (1) goods identity fully de-hashed (`stable_service_id`/`stable_projection_id` now keyed on `card_identity`, only node identity + `x-a2n.uid` or name+skill); (2) buyer favourite refresh `/v1/projections/refresh` (find the newest card by identity and update in place, say so honestly when not found); (3) trial bookkeeping made atomic (`LocalStore.tx()` BEGIN IMMEDIATE, counting and samples in one transaction, no lost updates under concurrency); (4) free-text redaction (both summaries and previews pass pattern-level redaction: email/phone/key/private key); (5) buyer read-only sample entry `/public/v1/samples` (public-benefit switch gate + paging/count cap/summaries first) + an active declaration before a trial call that "the delivery becomes a public sample by default". **R1 is done** (`a2n_sdk.trials.TrialBook` local trial/sample ledger + a call-completion side hook + `/v1/trials` + the console's supply detail "description + trial progress + delivery samples"; pytest all green, node console browser-level verification passing 0/10→10/10). **R2 has landed (2026-09-30)**: the design draft (identity basis: both buyer and seller use a node did, one did = one node; rules in [FEEDBACK-RULES.md](FEEDBACK-RULES.en.md) v0.1, interface contract a2n-feedback/1 in [FEEDBACK-API.md](FEEDBACK-API.en.md)) implemented batch by batch: **F0 local ledger** (`a2n_sdk.feedback.FeedbackBook`: one per direction + version chain + hard state gate + redaction + verified acceptance), **F0b local routing** (`/v1/feedback/open|revise`, with direction and counterparty decided by local facts rather than the client's claim), **F2 verified retention** (`a2n_node.feedback_identity` reusing the node's ed25519 identity to sign and verify), **F1 console** (a "bilateral feedback" panel + an inline "write/edit feedback" action on call records + a feedback dialog; quality cannot be reviewed before delivery; no score aggregation at all), **F3 piggyback exchange** (feedback travels with the **receipt acknowledgement**: `offer_feedback`/`absorb_feedback` + `PeerExchange.finalize`/`acknowledge` + `SignedA2ATransport._confirm`; feedback written after the call is delivered by resending the acknowledgement through `/v1/feedback/deliver`, reporting honestly whether it arrived), **three read-only interfaces** (`GET /v1/feedback` paged FeedbackView[], `GET /v1/feedback/{id}/versions` version chain, `GET /v1/feedback/summary` fact counts and per-dimension averages; the console's "versions / summary" buttons call them for real). **Still not done**: cross-internet two-machine acceptance. **R3–R5 not started.** Cross-internet two-machine acceptance is not done (this machine has no SSH channel; tracked separately, see R0 item 5).

**Coordination batch C0 has landed (2026-10-01)**: `a2n_sdk.coordination` (all a2n-coord/1 public data shapes, three-layer `Protocol` ports, state and verification enums, error-code→HTTP table, stable goods key `CandidateKey.of_card`, quota ledger `BudgetLedger`; zero third-party dependencies) + `a2n_node.coord_identity` (node ed25519 signing/verification of NodeRecord / Referral / coordination envelopes, with domains `a2n-coord-node/1` / `a2n-coord-referral/1` / `a2n-coord/1`; strict JSON parsing; node capability declaration). Verification: `tests/test_coordination_contracts.py` (23) + `tests/test_coordination_identity.py` (16). **Update 2026-10-05**: the runtime implementation of C1–C3 has landed; C4 has been wired to the local quantity policy, pause and resume, description and sample display, and channel import. Full SDK installation, self-check and start entry points are unified. Reputation and quality policy, and cross-internet acceptance, remain listed as incomplete.

**R0: fix the current blockers first, do not widen the feature surface**

1. Pass the real protocol type into the supply mount. greenlight uses the plain JSON adapter; a standard A2A upstream keeps using the A2A adapter. Verify that skill, payload, errors, long tasks and results are translated correctly at the boundary.
2. Goods identity is independent of the upstream address and temporary port. Demo supplies are stored stably by node identity and logical goods identifier; a card modification keeps its version. Changing the address updates the mount without resetting trials, samples and reputation.
3. For the duplicate old supplies in the current demo volume, design a one-off migration: back up first, identify old and new mounts of the same goods, stop publishing the old card, and keep existing call records and provenance. Handle only demo entries whose ownership can be confirmed, and never delete a user's supply because of a matching name.
4. A buyer that already imported an old card can refresh the address or get an explicit "updated" notice. Do not fix only the seller directory and leave the buyer's favourite permanently broken.
5. Accept host-to-container calls and calls between two computers on different networks separately. Cross-network first uses an actually reachable HTTP(S) path or the existing voluntary relay; native P2P traversal results are recorded separately.

Acceptance should run the real chain "directory discovery → local import → call → view result". Call each of the five normal cases once, verify the failure of the invalid case, and then check identity, card count and calls across at least three restarts. Host-and-container success cannot be written up as public-network two-machine success; recovery after going offline must not automatically replay tasks already sent.

Main landing points: `docker/node_entry.py`, `a2n_node/supply.py`, `a2n_sdk/runtime.py`, `a2n_sdk/upstream.py`, `a2n_sdk/management.py`. Add protocol combination tests and a restart regression with persistence; testing only that the greenlight handler returns a dict is not enough.

**R1: turn the first 10 services into the Agent's real track record**

The goods detail first shows: what it can do, input examples, delivery examples, the first 10 samples, trial progress and price; feedback and reputation come after. Signatures, credentials and network diagnostics sit where they can be expanded when needed.

The first-phase sample contains at least the requirement summary, the actual delivery content or a preview, the call time, the Agent version, the duration and whether it was free. Text and structured results are supported first; files get a safe preview and download, with images and audio/video added later. Large files load on demand and the public storage footprint is limited.

A sample is generated from a real call result, not gated on the buyer rating first, the seller uploading by hand or an application for review. Promotional examples the seller adds are clearly distinguished from free-trial samples. An initial sample is fixed to a specific call and version, and the seller cannot filter out unflattering deliveries by deleting and adding a good one. After the Agent is modified, show the version difference, and do not pass an old sample off as the new version's performance.

Before the first free call it is stated clearly that its delivery **will certainly be published as a sample**; this rule is part of the trial mode's fixed description and cannot appear only after the user has called. The public preview uses a content projection allowed to be public, removing keys, identity information and business privacy. **There is no prior gate of "do you agree to publish"** (ruled 2026-10-06): the first 10 free calls and "certainly public" are two sides of one thing, and having a gate would be allowing "given away but leaving no track record". When there is no publishable content, show the reason honestly and still occupy the sample slot; the seller must not pick good reviews under the pretext of privacy handling. Users are not required to upload proof or fill in complex authorisation forms for every transaction.

Trial counting and display follow:

- The first 10 completed calls are still free, not changed into a voluntary promotion, and not changed into 10 per buyer.
- A technical failure, a timeout with no delivery and a cancellation do not occupy a completion slot, but stay in the failure statistics.
- If delivery is complete but the buyer thinks it is bad, keep the sample and the feedback; a low score alone cannot delete this completion and demand that the seller stay free forever. How to map to the current `ACCEPTED/REJECTED` states needs a clear distinction between technical delivery and subjective evaluation at implementation time.
- A retry, polling and a repeated completion callback of the same task count once. When concurrency occupies trial slots, fix at acceptance whether this call is free; it cannot become a paid order only after the result comes back.
- Following the principle that "one more run barely matters", it is suggested that the few concurrent free orders already accepted when the trial ends stay free, with the seller's concurrency cap limiting extra consumption; the first batch of samples is fixed to the first 10 completions, with extra completions listed separately. The first phase does not need a complex reservation and reclaim mechanism to pin the 10th slot exactly, and it must not accept free orders without limit.
- Trial counting and sample identity continue across restarts, and changing the port or editing the description does not start over.
- Reconnect free sampling keeps the existing 5 calls, top-up frequency and cumulative grant cap, separate from the initial samples, and does not quietly reset into a new Agent.
- After the 10th, show the real trial-ended state; if the seller chooses to stay free, say so explicitly, and if the paid channel does not exist yet, show that payment is not enabled; do not charge automatically or permanently pretend to still be in the first trial.

A self-source call counts towards completions under the existing rules and does not count as independent reputation. Older documents did not publish self-source cases; whether to keep a display slot labelled "seller self-test" on the new sample page is a display detail still to be decided, and a self-source case must not be called an independent buyer's sample. When samples are insufficient, explain why, and do not fabricate 10 independent reviewers.

Main landing points: the product node's local storage and call-completion path, the supply projection and management interfaces, `web/runtime.html`. The existing `calls` keeps private raw input and output, while samples keep the public selection, preview and version reference, avoiding a duplicate "success record" that no longer follows the original delivery.

**R2: credible bilateral feedback first, complex scores later**

> Executable rules and interface contract: [FEEDBACK-RULES.md](FEEDBACK-RULES.en.md) v0.1 + [FEEDBACK-API.md](FEEDBACK-API.en.md) (a2n-feedback/1), established 2026-09-30. The identity basis is decided: both buyer and seller use a node did, one did = one node. **Implementation progress: F0/F0b/F2/F1/F3 + the three read-only interfaces have landed** (local ledger + local routing + node identity verification + console + piggybacking and supplementary delivery with the receipt acknowledgement + `GET /v1/feedback`/`versions`/`summary`); cross-internet two-machine acceptance is not done.

| Direction | What is asked first | Misjudgement to avoid |
|---|---|---|
| Buyer → Agent | Whether the result matches the description, is useful, and was delivered on time; optionally one sentence of reason | Technical success does not automatically generate a positive review; with no result, content quality cannot be reviewed |
| Seller → buyer | Whether requirements were as agreed, cooperation and communication, whether there was repeated malicious consumption; payment behaviour is added once payment exists | Giving a low score, raising a reasonable objection, a normal cancellation or a missing review is not dishonesty |

A transaction has only one currently valid feedback per direction; edits keep versions, and influence is not amplified by repeated submission. Completed, failed and cancelled tasks may all leave feedback corresponding to their state: a failed order may review reachability or service experience, but cannot review the quality of a finished product that was never produced. Someone with no call relationship cannot review out of thin air.

Bilateral feedback is written independently by default. It is suggested to publish after both submit, or after an agreed review window ends, to reduce retaliatory bad reviews; the window length is finalised in a small pilot. Not reviewing stays "not reviewed", is not treated as a positive review, and does not block the call, trial completion or settlement.

The first version records a few dimensions, a reason, time, task link and source. Receipt, agreement and payment state are still stored separately, but normal use does not require both sides to hand-sign complex materials step by step. Feedback is signed automatically with the node's existing identity, so the other side cannot forge or rewrite a review; disputes and replies feed later reputation reference without directly declaring anyone malicious.

Node calls with authenticable identities on both sides enter the sourced reputation statistics; an ordinary third-party call lacking an authenticated buyer identity can only be shown as an unverified experience record. Different DIDs only mean different identity labels and do not prove an independent person behind them; the counterparty count in the interface must state this basis.

Main landing points: the local feedback service and storage in `a2n_sdk`, identity signing and exchange in `a2n_node`, and the product console. The task linkage, identity checks and deduplication ideas of the old `ratings.rate` can be reused; its rules of allowing only buyer-rates-seller and only successful orders to be reviewed are not copied directly.

**R3: use reputation to change later opportunities, and leave a door open for newcomers**

An Agent's delivery quality accumulates per stable goods identity and version; a seller's performance accumulates per node identity; a buyer's cooperation accumulates per buyer identity. One seller's several Agents should not automatically share an identical quality score, and renaming goods cannot erase one's behavioural history.

First show dimension statistics, sample count, number of independent counterparties, recent performance and update time. No history means insufficient information, not a default bad service. Call volume, online time, technical success and subjective praise are not directly blended into a personality total score.

The algorithm goes live in this order:

1. Collect facts and recompute offline: first observe the deviation using real feedback and simple aggregation. The old `normalize_score` is a candidate, but the old fixed bonus for success events is not brought into the product yet.
2. Limit the influence of a few raters: contribution from repeated transactions between the same buyer and seller is capped; a known self-source does not count as independent reputation; small samples shrink towards a neutral estimate while showing the uncertainty.
3. Build an observational version of the recommendation order: record "how the algorithm would have ranked" and why, compare it with actual reuse and buyer satisfaction, and only enable the default ordering once there is no obvious harm to new Agents.
4. Apply it to local selection: first satisfy skill, reachability, user budget and similar conditions, then refer to delivery performance and reputation; keep a limited exploration opportunity for qualifying Agents with few samples. Users may change the ordering or block a party themselves, and sellers may set acceptance conditions.
5. Persistently poor performance may gradually lower ranking, automatic call frequency or the quota a given user is willing to bear; later good performance allows recovery. A single bad review does not directly create a network-wide ban, and a normal quality disagreement is handled separately from clear protocol forgery.

Cross-node feedback needs a data path, otherwise everyone only has reviews they know about themselves: both sides keep signed feedback, and samples/feedback summaries are fetched on demand at discovery time; sourced records may be supplemented from the buyer or from a public node that declares support for that data service, and recomputed locally. Use paging, validity periods and size limits, and do not synchronise the whole network's tasks. A summary provided only by the seller states its source and coverage; not seeing negative records cannot be used to infer they never happened. Public services are replaceable and hold no final evaluation authority; mandatory public discovery does not require every node to cache the full review history.

Sock puppets and collusion cannot be eliminated entirely by one rating formula. The first phase limits their influence, raises the cost of repeated forgery and exposes the sample structure, observing whether they can still systematically beat normal transactions; no promise is made that the algorithm identifies every malicious user.

Acceptance adds these counter-examples: a seller calling itself 100 times, a fixed pair inflating each other, one person repeatedly giving bad reviews, a buyer's reasonable rejection, a network failure mistaken for poor product quality, an old Agent updating its version, a seller providing only positive reviews, and a newcomer with no samples. Each case looks at how ranking and both sides' acceptance opportunities change.

**R4: wire in lightweight small-amount payment, letting both sides set their own risk**

The first phase supports a fixed per-call price, one currency and one real payment method both sides can use. Before calling, the buyer sees the price, budget ceiling and whether it is in the free period; the seller sees this call's payment conditions and can set concurrency and a cumulative unsettled amount cap. A snapshot of the price is taken when the call starts, and a retry reuses the same task to avoid accidental double charging.

The direction of "both sides settling directly, or through the chosen payment channel" is kept: A2N takes no commission and holds no funds. A receipt is shown only after a channel-verifiable record of actual receipt exists; a buyer claiming to have paid, or both sides noting an amount due, cannot be written directly as receipt. If an external channel has costs, show in advance who bears them; A2N's zero transaction commission must not be passed off as meaning every channel is free.

Buyer and seller may bear an unsettled amount and an unsatisfactory delivery within an explicit limit, and choose rework, a negotiated refund or leaving an objection under simple rules shown in advance. An objection is automatically linked to this call, can notify the other side and be answered, and gradually enters the reputation statistics; an ordinary small transaction does not take complex guarantees, strong arbitration or a field-by-field hand-signed contract as a precondition.

If no payment method is available, paid calls are explicitly unavailable, and the listed price and `NoSettlement` are not packaged as a completed payment. Withdrawal, cross-currency atomic trading and automatic forced refunds are not acceptance criteria for this batch.

**R5: verify in a small network whether reputation is useful**

A suggested pilot starting point is 3 independent suppliers, at least 10 users and one scenario with a clear need; the scale is a recruitment target, not an admission condition that forbids smaller networks. Real workflows can be wired in from existing goods-detail-style cases, with input and delivery previews made easy to understand first. Deterministic demos continue to be used for testing and do not count as evidence of real service quality.

Key observations: the proportion of searches that find a usable service, the real delivery rate, the proportion of viewable samples, the time needed for the first 10 completions, bilateral feedback coverage, the proportion of users choosing the same Agent again, the waiting time for a new Agent's first call, and improvement and false positives after a bad review. Once payment is enabled, add the actual settlement ratio and unsettled amount; A2N revenue is not a metric.

These observations distinguish free/paid, test/real calls, self-source/other sources, and technical failure/quality dissatisfaction. Record a baseline first, then set ranking weights and thresholds; do not use a preset high score to prove the market already works.

**Implementation division and reuse boundaries**

| Location | Responsibility |
|---|---|
| `a2n-kernel` / `a2n-p2p` | Hash and signature foundation, peer discovery, UDP hole punching and sealed relay |
| `a2n-acceptance` | Acceptance policy and templates (pure rules, no database dependency) |
| `a2n-sdk` | Product runtime, call facts, local trial and sample service, feedback entry, console; keeping the zero-a2n-dependency constraint |
| `a2n-node` | Assembling the runtime, identity signing, public interfaces for samples and feedback, on-demand exchange |
| ~~`a2n-reputation` and other old domain packages / `a2n-server`~~ | Retired wholesale on 2026-10-05; reusable pure computation was merged into the SDK (`dimensions`/`media`/`pricing`), and old platform business does not substitute for product node acceptance ([RETIRED-PLATFORM.en.md](RETIRED-PLATFORM.en.md)) |

If algorithms such as normalisation need to be shared, first extract a computation interface with no database dependency and inject it through assembly; update the layering guard test at the same time. Use existing call facts as the data source, adding trial state, public sample metadata, feedback events and a recomputable reputation summary. There is no need to split out another set of packages or build a global ledger first.

The technical mapping continues to use Agent Card for service declarations and Task/Artifact for tasks and deliveries, with A2N samples and feedback carried through extensions; these objects are already defined in [the A2A 1.0 specification](https://a2a-protocol.org/v1.0.0/specification/). The degree of full compatibility with the standard needs separate testing, and this roadmap does not claim compatibility has passed on the strength of a name.

**Three details to finalise before implementation, none blocking R0**

1. Charging conditions after 10 completions: the old graduation interface additionally required "at least 3 non-self-source cases, no open disputes, a complete acceptance template", while the free determination at order creation had already expired once the quota ran out; the two are inconsistent. It is suggested that whether the 11th call charges be decided uniformly by the seller's explicit quote, payment availability and what the buyer saw beforehand, using reputation confidence for selection hints, so that few samples or a single objection do not block transactions indefinitely; this is a suggested adjustment to the old rules and should be stated separately before implementation.
2. Display of self-source samples: keep the two rules of counting towards trial completion and excluding from independent reputation. It is suggested to allow showing "seller self-test" and distinguishing independent samples, instead of hiding the content entirely; whether to adopt this needs a separate decision.
3. Rating cold start: the old rules published a score only after 20 ratings by a rater and 20 raters for a supplier. It is suggested that samples be viewable immediately, raw feedback shown under the publication policy, and the aggregate score carry sample count and confidence, with low samples influencing ranking little; thresholds and the review publication window are set after real feedback arrives, not silently changed in this round.

**The concrete starting point for the next development round**

The first batch of changes delivers only R0: fix the protocol → stabilise goods identity → migrate the confirmed old demo mounts → real calls and restart acceptance. Then deliver R1: local trial state → public sample projection → description and sample detail page → acceptance from 0 to 10 real calls. R2's feedback fields and interfaces can be decided together while designing R1, with scoring and ranking going live once there is feedback.

Each batch writes the demo output, test results and remaining boundaries back into the progress; a status only changes when the planned item is complete. When this document was created R0 was still incomplete; as of now **R0 and R1 are complete** (see "Progress" above).
