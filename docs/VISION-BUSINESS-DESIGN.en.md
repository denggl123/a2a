<!-- source: b8de9cff85a3 -->
# Completing the vision: detailed business design

Version: v0.1 · 2026-10-05 (Asia/Shanghai). Status: **a business design draft awaiting implementation**.

Based on the direction the originator has made explicit: public benefit, zero transaction commission; every node is a platform and carries a bounded public discovery duty; an Agent is shown mainly by its description and real cases; the first 10 free calls form samples by default; both buyer and seller give feedback; reputation over evidence, with a single transaction allowed to lose within limits.

The invariants in this document implement that direction; the new flows, algorithms and thresholds are design proposals, not yet treated as confirmed by the product side, and do not mean the code implements them. For the architecture and interfaces see [the detailed architecture design](VISION-ARCHITECTURE-DESIGN.en.md). Completion markers for the old platform in the historical vision are not proof of completion for this product.

## 1. Goals and the definition of done

The complete business loop: **list → describe and sample → discover and choose → fix transaction conditions → deliver → both sides give feedback → local reputation judgement → change later opportunities**.

Success means users find suitable services more easily, good services are chosen again more easily, the damage from malicious behaviour is contained, and a new Agent has a chance to build a track record. A2N's revenue, fees and platform-coin price are not success metrics.

| Vision | Current foundation | What this design completes |
|---|---|---|
| Reputation over evidence | Bilaterally signed feedback, version chain, per-dimension averages | Recomputable dimension reputation, information sufficiency, local ranking and acceptance policy |
| Fair competition | Stable goods identity, real transaction linkage | Caps on repeated-counterparty influence, self-test isolation, exposure for newcomers, gradual limitation and recovery |
| Knowing reputation before trading | Both sides keep their feedback | On-demand multi-source queries, provenance and coverage, anti-selective display |
| A sample is a track record | Redacted summaries of the first ten successful deliveries | Technical delivery separated from quality, bad results kept as samples, self-test source, multimedia display |
| A single transaction may lose, but within limits | Quoting, partial budget checks, task idempotency | Conditions fixed before acceptance, business risk quota, control of unsettled amounts, lightweight negotiation |
| Real buying and selling | Pricing and the NOT_CONFIGURED state | Both sides choosing a channel and settling directly, real payment and refund facts |
| Satisfied means stop, keep looking for paths | Quantity policy, pause and resume, known-channel ordering | Local business preferences, experience information, explainable stop conditions, real public-network verification |
| One-click full installation | Five-package install, self-check, console | Automatic joining, residency, upgrade recovery, finished-product input and output interfaces |
| The former later-stage vision | Rules recorded | Free sampling on reconnect; safe sample Agent deployment and creator revenue sharing |

## 2. Invariants and roles

### 2.1 Invariants

1. A2N takes no transaction commission, holds no transaction funds, and issues no points meant to pass as income.
2. Public discovery sells no ranking or exposure; helping others discover cannot be exchanged for goods ranking or business reputation.
3. Every online node on the network carries a quota-limited duty of handshake, referral and card lookup; not listing or pausing orders does not cancel that duty.
4. Discovery, looking at samples, checking feedback, read-only probing and channel selection do not execute the Agent, do not consume free slots and do not charge.
5. Delivery, quality and payment are recorded separately; a successful connection does not mean good quality, and delivered does not mean paid.
6. Reputation changes future choices; it cannot rewrite past facts or reinterpret transaction conditions already agreed.
7. A single bad review, a normal rejection, a reasonable cancellation, not reviewing, and normal use of the free quota are not automatically judged malicious.
8. One DID is one node identifier and does not prove an independent natural person. A signature proves provenance, not that an opinion is fair.
9. Local policy cannot automatically create a network-wide ban, and cannot guarantee identifying every sock puppet and collusion.
10. User input, account credentials and private deliveries are not automatically published to the whole network; what is published is an explicitly declared safe projection.

### 2.2 Roles and rights

| Role | Rights and responsibilities |
|---|---|
| Buyer node | Express needs, choose services, set budgets, leave feedback and objections, keep its own delivery records |
| Seller node | Describe services, set prices and acceptance conditions, provide samples, review the cooperation, keep its own facts |
| Agent goods | One stable supply; quality is observed per goods and version, not conflated with all the seller's goods |
| Public node | Provide discovery within a declared quota; may voluntarily provide a mailbox, witnessing, caching or task relay, with no final evaluation authority |
| Local user | Decides its own trust rules and automatic call authorisation, may change ranking, selection and blocking, and need not obey anyone else's algorithm |
| Creator | Later provides installable Agent packages; the revenue split is agreed in advance by the participants and does not change A2N's zero-commission principle |

The same node may buy, sell and provide public services at the same time. Business reputation is at least split into three perspectives: goods delivery, seller performance and buyer cooperation.

## 3. Goods and user experience

### 3.1 Listing declaration

The stable goods identity remains `(provider_did, service_id)`. Changing the port, description, price or upstream address does not create a new goods; a version update keeps history and shows the version difference.

A goods declaration contains: purpose and limits, input form, output type, examples, service version, response-time declaration, acceptance rules, a fixed per-call price or an explicit free offer, trial rules, the publishable sample scope, and order capacity. Network measurements and seller self-reports are shown with their freshness separately.

The first phase of charging supports only a fixed per-call price in one explicit currency, avoiding a bill changed after execution by unpredictable usage. Multi-dimensional usage is recorded as facts first, with verifiable billing wired in item by item later.

### 3.2 Buyer flow

1. Find a capability by natural description or service category; the ordinary usage form does not require the user to hand-write a Card or JSON.
2. First look at the service description, real samples, version, information sufficiency and one's own past experience.
3. Then look at whether this call is free, the fixed amount, who bears the cost, expected delivery, the public sample rule and the risk ceiling.
4. The user may confirm a single call, or authorise in advance "these service types, up to this amount, under these risk conditions, call automatically".
5. Output is shown as text, images, audio/video, files or structured tables; technical details go into an expandable diagnostics area.
6. After the result, the user may review, raise an objection or use it again; not reviewing does not block settlement or trial progress.

### 3.3 Seller flow

Connect an already working local workflow or HTTP/A2A service, fill in the goods description and form, set capacity and charging conditions, preview and list. The SDK does not execute or publish an Agent the owner has not chosen merely because installation succeeded.

The management page highlights pending tasks, trial progress, samples, counterparty feedback, unsettled amounts and the reason for pausing. When the network is unreachable at first listing, it explicitly prompts to configure a direct connection or choose an available relay; it must not show "listed" without any actual call path.

## 4. Transaction facts: recorded separately to avoid misjudgement

A transaction uses a stable `trade_uid`, bound to the buyer DID, seller DID, goods ID and task ID. Changing the input or accepted conditions under the same key reports a conflict and cannot become a second execution or charge.

| Axis | Suggested states | Purpose |
|---|---|---|
| Execution | CREATED, ADMITTED, RUNNING, DELIVERED, FAILED, CANCELED, UNKNOWN | Whether it was actually delivered; UNKNOWN does not imply it did not execute |
| Quality | UNMEASURED, RULE_PASS, RULE_FAIL; subjective dimension feedback stored separately | Rule acceptance and buyer opinion expressed separately |
| Free eligibility | FREE_INITIAL, FREE_RECONNECT, FREE_VOLUNTARY, PAID_FIXED | Fixed at acceptance, not revoked on completion |
| Payment | NOT_REQUIRED, UNAVAILABLE, DUE, PENDING, CONFIRMED, FAILED, UNKNOWN | Whether the channel really confirmed receipt |
| Refund | NONE, REQUESTED, AGREED, PENDING, PARTIAL, CONFIRMED, FAILED, UNKNOWN | Linked to the original payment, without erasing the original delivery |
| Objection | NONE, OPEN, RESPONDED, AGREED, CLOSED, WITHDRAWN | Keeps the disagreement and how it was handled, without impersonating third-party adjudication |

Existing task states such as `ACCEPTED/REJECTED` may still be displayed for compatibility, and a new fact axis must not simply use `ok=false` to conclude there was no delivery. Only a checkable technical completion fact is recorded as DELIVERED; with no result or no way to confirm, UNKNOWN is kept.

## 5. The first ten free calls and samples

### 5.1 Initial free rules

The initial 10 are counted by stable goods identity; the free period cannot be exited through the goods card, and a change of version, port, upstream or description does not regain a first-batch slot. The seller may voluntarily stay free; the initial period must be completed before charging.

It is suggested that "completion" be defined explicitly as actual technical delivery, independent of subjective satisfaction and of whether rule acceptance passed:

- Delivered but failed rule acceptance, or the buyer is dissatisfied: counts as one completion, keeping the sample and the quality result.
- Network failure, an execution exception with no delivery, or a confirmed cancellation: does not count as a completion, with the failure fact recorded separately.
- Timed out with an unknown result: not counted yet; a late completion is verified and then recorded once against the same transaction.
- Calling yourself: may count as a completion, but the sample must be marked "seller self-test" with zero weight in independent reputation.
- Repeated callbacks, replays, polling and channel switches of the same transaction: counted once only.

This is a suggested adjustment to the existing "only a passed acceptance forms a sample". Business quality failures should be visible, while a subjective rejection must not force the seller into unlimited free service.

### 5.2 Free conditions fixed at acceptance

Before accepting, the seller reads the completion progress and atomically fixes this call's free reason and capacity occupancy; the buyer receives the signed conditions. Once progress passes 10, free orders accepted earlier are still free.

Bounded overflow is suggested: with progress at 9/10 and a free concurrency cap of 2, both accepted orders are free; the first 10 samples are fixed in order of first technical completion, and extra free deliveries are listed separately. Each free acceptance is also limited by the seller's total concurrency, execution deadline and resource budget, and "one more is allowed" cannot justify unlimited acceptance.

A free declaration does not mean the external paid upstream becomes free: the seller is responsible for the upstream cost it chose, and may refuse requests beyond its own resource budget before accepting. A2N's free condition for the buyer cannot have internal API fees appended after completion.

### 5.3 Sample content and non-selectivity

Each fixed sample slot contains the goods/version, technical completion time, a safe summary of the requirement, a safe preview of the delivery, the source category, the quality rule result, the free reason, the content hash and the transaction link.

A public sample does not show the buyer's DID or the original task ID by default. Source category and third-party verifiability are shown separately: a seller-signed "node transaction" label does not prove an independent user, and a sample label alone cannot add public reputation weight. A review that needs to publish the transaction role is explicitly chosen by the author at publication time.

The seller cannot delete a bad sample and add a good-looking one. Public cases may be added later, but must be labelled "supplementary case" and must not impersonate the original first ten. A new version inherits the node's behavioural record; an old version's quality is not directly the new version's quality; the first batch is not reset by renaming or a version number change.

A privacy hide records the location, time, source, reason for hiding and the change history, and does not pretend "this delivery never happened". The hidden proportion affects how much information is visible and does not automatically equal fabrication. Seller self-tests, bilateral node transactions and unverified external calls are counted separately; a count of different DIDs is not called "number of independent people".

### 5.4 Public projection and media

Before the call it is stated clearly that first-batch free deliveries **will certainly be published as samples** (ruled 2026-10-06, with no prior gate of "do you agree to publish"). The original input and original delivery stay private; only fields allowed to be public, redacted summaries and thumbnail previews are published.

Field-name and text-pattern redaction cannot prove that an image, video or business text has no privacy in it. A file with no declared safe public fields, or one that cannot be judged, shows a placeholder and safe metadata such as type/size/version by default and waits for an authorised party to confirm the preview; the original file must not be uploaded by default. Hidden content may carry an explanatory note and does not change the original fingerprint.

The sample page supports text, image thumbnails, audio/video previews and file metadata. File access is limited by size, type, bandwidth, validity period and permissions; public discovery is not used to host large files without limit.

### 5.5 Free sampling on reconnect

As a separate later batch, it is suggested to keep the earlier direction: coming back online provides at most 5 free stability samples, each grant at least 24 hours apart, with a lifetime cumulative grant of at most 30 per goods (including the initial 10). The specific thresholds await a pilot, and "a real disconnection was detected" is not a precondition for granting.

Counting is bound to the stable goods identity and only grants to goods whose initial period has ended; the same reconnect event is idempotent, unused reconnect quota does not stack, and no further grants are made once the lifetime cap is reached. Goods still in their initial period use their remaining quota and do not record reconnect separately. A node reconnecting several goods at once is still bounded by the total resource budget.

Reconnect sampling does not reset the initial 10 or the original quality record; network stability is listed separately, and delivered content plus valid subjective feedback remain viewable but are not mixed into the new version's initial quality samples. This feature is not implemented in the new node.

## 6. Bilateral feedback and handling disagreements

### 6.1 What is evaluated

| Direction | Dimensions and boundaries |
|---|---|
| Buyer → goods | Description match, usefulness of the result, delivery timeliness; content quality can only be reviewed after a confirmed delivery |
| Buyer → seller | Communication, the performance process; network measurements and human performance are kept separate |
| Seller → buyer | Requirements as agreed, cooperation experience; after payment, payment performance with channel facts may be added |

A failed order with no content may be reviewed for service experience, but a finished product that does not exist cannot be reviewed. A transaction with a confirmed delivery but a rejected acceptance allows quality feedback. Using the free quota, a normal cancellation and raising an objection are not malicious facts in themselves.

Each transaction has one current opinion per direction; edits keep the version and the reason, and old versions do not count again. Not reviewing means a missing opinion. It is suggested that the first version keep independent writing and timely exchange, with retaliatory bad reviews mitigated by weight caps, replies and multi-source observation; a mutual closed review window is a pilot option and adds no required step before the call.

Reviews are opened only once the transaction facts can bind the author's role; an unfamiliar node that made no call is not allowed to rate directly. The object and dimension are checked separately: a failed order's process opinion, a delivered result's opinion and a channel-verified payment fact cannot substitute for one another.

### 6.2 Publication and replies

The review author chooses the public dimensions and a safe short comment, or makes it visible only to the two parties; a private review may be used for one's own choices and does not enter strangers' public reputation. The seller cannot delete the buyer's original signed review, and the buyer cannot rewrite the seller's review.

A public record keeps the author, transaction link, version and correction chain; text privacy can be hidden later through an appended projection update. The coverage and update time of a third-party cache must be visible, and no promise is made that every historical public copy disappears immediately.

If something already public becomes private, the author appends a signed public retraction marker; nodes that support the revision no longer count the old opinion in the public score. The original private full text is not published as proof of retraction, and one's own private experience may still be kept.

A reply is a new linked record and does not overwrite the counterparty's opinion. A reply may explain the requirement, the network, the delivery and the solution, and does not become mutual agreement merely because one side claims it "is resolved".

### 6.3 Lightweight objection flow

OPEN → notify the counterparty → RESPONDED → propose rework/refund/explanation → each side accepts or rejects → CLOSED or left unresolved.

Protocol transmission and receipt state are listed separately; if the counterparty is offline, show "not yet delivered". A withdrawal only appends a fact. Reaching a resolution requires explicit confirmation from both sides, and a rework is a linked new task; the original task must not be replayed and passed off as rework.

OPEN or unresolved does not mean anyone lost, and does not directly deduct reputation or refund money. An actual refund must be confirmed by the original payment channel. Subjective disagreement, explicit signature contradictions and channel-confirmed late payment are handled separately. Ordinary small orders do not take third-party strong arbitration as a necessary precondition.

## 7. Cross-node experience information

### 7.1 Data sources

When a buyer discovers goods, it may query on demand the samples the seller published, records the review authors published themselves, known public caches, and its own existing transaction facts. The same rules apply when a seller reviews a buyer.

A public cache forwards the original author's signed object; it cannot sign an opinion on someone's behalf or compute a network-wide score that must be accepted. The same original record appearing in several caches counts once.

### 7.2 Queries and coverage

Fetch a small summary first, then records or samples on demand. Each query has a budget of nodes, items, total bytes, duration and validity period; the whole network's task history is not copied.

The result shows the subject, version range, source list, observation time, number of public samples, number of hidden samples, number of verifiable reviews, source coverage, and expiry or missing cases. "The seller provided no negative reviews" must not be read as "there have never been negative reviews". With only the seller as a source, information sufficiency is lowered, and cheating is not concluded from that alone.

Retractions, revisions and version conflicts have explicit records. When sources conflict, keep the provenance and mark it as awaiting reconciliation; do not arbitrarily pick the better-looking version.

## 8. Local reputation algorithm

### 8.1 Three kinds of subject, several dimensions

1. Goods quality: computed per goods, skill and version for dimensions such as result usefulness and description match.
2. Seller performance: computed per seller node for dimensions such as the performance process and communication; network stability is shown separately.
3. Buyer cooperation: computed per buyer node for requirements as agreed and cooperation experience; payment facts are wired in once real payment exists.

No global personality total score is generated. The output contains the per-dimension signal, effective support count, information sufficiency, recent change, source coverage, algorithm version and exclusion reasons. A technical success rate is not a subjective quality score, and price does not directly change reputation.

### 8.2 Eligibility for inclusion

- The signature is valid and the author's role matches the transaction subject; the transaction link is checkable.
- Only the currently valid version is used; duplicate copies and old revisions do not contribute again.
- A known self-test has zero weight in independent reputation, while the self-test display is still kept.
- Unverified calls and opinions with no checkable transaction relationship are marking information only and are not mixed into valid reputation.
- A network failure produces no quality score; unconfigured payment produces no "payment integrity" fact.
- A data source can only say which public records were seen, not prove completeness.

### 8.3 Suggested first-version formulas

Per dimension, map the 1–5 score to `x=(score-1)/4`. For each qualifying record compute a base weight:

```text
q_i = source eligibility coefficient × freshness decay × current-version correlation coefficient
freshness decay = 2 ^ (-days since / H)
w_i = q_i × min(1, C_pair / sum of q for the same counterparty in this dimension)
```

Suggested pilot values `H=90 days`, `C_pair=1`; a known self-test has an eligibility coefficient of 0. Old versions are shown separately by default, and a merged reference may only lower the correlation coefficient under an explicit policy.

Eligibility and version coefficients take values 0–1, the age floor is 0 so that a future timestamp cannot produce a decay weight above 1; an anomalous future timestamp is checked separately for eligibility. Decay is based on the checkable original transaction time, and editing a review does not refresh the transaction's age. When clocks and sources are unreliable, information sufficiency is lowered; freshness cannot be proven by the other side's self-reported date alone.

Grouping by counterparty, `W_p=sum(w_i)`, `M_p=sum(w_i*x_i)/W_p`:

```text
dimension estimate = (m×0.5 + Σ(W_p×M_p)) / (m + ΣW_p)
effective counterparty support = (ΣW_p)^2 / Σ(W_p^2)
```

Suggested neutral prior `m=2`. With no data the estimate stays neutral, the interface shows "insufficient information" rather than a bad service, and small samples have limited influence. Effective support must be computed per counterparty group; 100 reviews from the same counterparty must not be mistaken for 100 independent supporters.

Zero-weight records and groups are excluded before division; with no valid group the estimate is 0.5, effective support is 0, and it is marked as insufficient information, avoiding division by zero or fabricated scores from an empty set or an all-self-test set.

Also report `total effective weight=ΣW_p` and the number of contributing counterparty groups. Many very old records may give a large group count with very little actual weight; opportunity limits check support, total weight, freshness and coverage together, not just the number of counterparties.

This estimate is only a local decision signal, not a "true quality probability" or a validated confidence interval. Different DIDs still do not prove independent persons. The first version does not let raters' reputation recursively determine everyone's reputation, avoiding a mutually inflating loop.

### 8.4 Anti-gaming and source limits

Contribution from the same counterparty is capped; a new revision by the same author replaces the old one; concentrated registration in a short period and mutual reviews within a fixed group are marked as a correlation risk, with influence limited first and fabrication not declared outright. Correlation-detection heuristics must have a recorded reason and an option to switch off.

Several caches from the same original author are not several votes. "Forwarded many positive reviews" cannot be treated as a public node's own reputation capital. Strong real-name verification, staking or expensive proofs must not be required for new users to take part.

### 8.5 Shadow run before enabling

First recompute offline and record "how it would rank if enabled", observing reuse, satisfaction, false positives and a new Agent's waiting time for its first call. Users can view the explanation, and an algorithm failure can fall back to the original quantity or manual policy.

Default opportunity adjustment is enabled only after adversarial cases and a real small-network pilot show the rules are useful. The algorithm and policy are versioned, and every change can be recomputed from the same facts.

## 9. How reputation changes opportunities

### 9.1 Buyer selection

First satisfy skill, input/output, budget, allowed region or channel, personal blocking and similar conditions, then refer to one's own usage history, dimension reputation and information sufficiency.

It is suggested that about 15% of display positions be kept for Agents that meet the hard conditions but have insufficient samples; the proportion awaits a pilot. Exploration changes display and candidate order and must not spend money on calls by itself. Source rotation, a per-seller display cap and a stable random seed together limit flooding. Public infrastructure does not sell these positions.

### 9.2 Seller acceptance

Total concurrency, per-buyer in-flight amount, free resource budget, unpaid amounts on credit and minimum cooperation conditions are configurable. A new buyer may enter a small-quota trial path and is not refused outright for having no history. A refusal returns an explicit reason and does not automatically become a bad review of the buyer.

### 9.3 Gradual limitation and recovery

It is suggested that local opportunity policy be divided into NORMAL, OBSERVE, LIMITED, MANUAL_ONLY, BLOCKED_LOCAL. Insufficient information does not automatically enter a punitive state; persistently supported poor performance may gradually reduce automatic selection frequency and quota, and later performance plus time decay allows recovery.

BLOCKED_LOCAL is decided by the local user, or triggered by an explicitly declared high-risk rule in advance; it cannot automatically propagate into a network-wide ban. An invalid signature can only be attributed to the authenticable actual sender, and must not deduct the reputation of the impersonated victim. A business restriction does not cancel a node's bounded public discovery duty; protocol abuse may be rate-limited independently.

Limitation lifecycle implemented on 2026-10-08: when LIMITED or MANUAL_ONLY forms, store the qualifying fact digest, algorithm and policy version, decision time and a 30-day validity period. A later source going offline, missing pages or insufficient budget does not constitute grounds for recovery, and the most recent well-supported limitation is kept; an incomplete query cannot aggravate the limitation either. Recomputing the same batch of facts does not extend the period; only new qualifying low-quality transactions may escalate or extend it. Valid revisions, retractions or support decay in a complete query may trigger recovery. On expiry it drops to OBSERVE and requires review; the passage of time is not interpreted as proven integrity. A manual block by the owner is lifted by the owner. The default is still shadow mode.

### 9.4 First-round shadow policy example

The table below is only a parameter draft for offline comparison, and **does not enable limits by default yet**. Before actually enabling it, observe real users and distinguish the applicable dimensions of goods quality, seller behaviour and buyer cooperation.

| State | Example entry condition | Effect on this machine's future transactions |
|---|---|---|
| NORMAL | Insufficient information, or current signals meet the owner's requirements | Keep normal selection; newcomers take the limited-risk path |
| OBSERVE | At least 3 contributing counterparty groups, total weight ≥2, applicable dimension estimate <0.4 | Note recent performance, add confirmations; do not refuse outright or drop to zero exposure |
| LIMITED | At least 5 contributing counterparty groups, total weight ≥3, estimate <0.35, and new qualifying facts supporting a persistent problem | Reduce automatic selection opportunity, in-flight amount or unsettled quota, keeping a manual and small-quota recovery path |
| MANUAL_ONLY | A risk ceiling set by the owner is triggered, or there is a verifiable condition conflict to resolve | Pause the corresponding automatic transactions, show the risk reason explicitly; do not call the counterparty malicious |
| BLOCKED_LOCAL | The owner blocks, or a serious risk rule the owner explicitly authorised triggers | No more transactions on this machine; other nodes judge for themselves |

Recomputing the same batch of opinions repeatedly cannot advance the "persistent problem" state. Recovery compares newer facts, decay and policy: for example, recover step by step when the estimate returns to ≥0.6 with recent support; when old support decays below the threshold, return to unknown and small-quota observation, not treating the passage of time as proven integrity. A manual block by the owner is lifted by the owner. All thresholds await calibration.

Local first-hand experience may adjust one's own risk quota separately, but the same transaction must not be counted both as public feedback and as a second independent counterparty. The scope of a restriction binds the actually affected goods or behaviour, and one low-scoring goods does not block all the seller's goods.

## 10. Transactions and payments allowing limited loss

### 10.1 Acceptance conditions

This call's conditions fix the goods version, input fingerprint, charging mode, currency, amount, who bears the cost, the free reason, the delivery window, the cancellation/rework/refund rules and the risk policy version. Both signatures complete automatically; the user is not required to hand-sign field by field.

A quote's validity period is suggested at 60 seconds; an expired quote is refused before acceptance, and an accepted order is not repriced because the quote expired. Repeating the same transaction returns the original conditions; reusing the task ID with different content reports a conflict.

### 10.2 Risk quota

Buyer: maximum amount per transaction, daily budget, in-flight funds reservation, unresolved loss ceiling and automatic call authorisation.

Seller: total concurrency, free running budget, unsettled quota per buyer, total unsettled quota across all buyers, and the longest waiting time. The total quota prevents the other side from bypassing the per-buyer quota by repeatedly switching DID.

An insufficient quota is refused before execution; delivery does not happen first with payment conditions added afterwards. When the funds state is UNKNOWN, keep the reservation and query; do not treat it as a failure, release it and pay again. Manual judgement and temporary suspension of acceptance are supported, and another person's funding commitment cannot be extended indefinitely.

### 10.3 Small-amount settlement

The first phase has a free mode and one fixed-price direct settlement mode. Prepayment or credit is chosen by the connected channel's capability and by agreement between the two sides; several complex financial products are not launched at once.

The first round suggests small-amount credit with a total unsettled cap: DUE is created after a confirmed technical delivery; a confirmed failed order with no delivery produces no service fee. Subjective dissatisfaction or a rule failure does not automatically waive payment or refund, and is negotiated under the handling conditions stated before the call. If the pilot channel only supports prepayment, a separate prepayment state machine is used, and the credit flow is not disguised as prepayable.

Channel costs are explained before the call; A2N's commission is always 0. Only channel-verified receipt may be marked CONFIRMED; a buyer's claim or a bilateral note of an amount due cannot impersonate receipt. A payment awaiting confirmation does not mean deliberate non-payment.

A refund is executed by the party with refund authority through the original channel. Automatic refunds apply only to cases agreed in advance, with a verifiable driver and a capped amount; an unresolved quality disagreement does not automatically generate a refund. Without a payment driver, charging is explicitly unavailable, and NOT_CONFIGURED cannot be treated as a success condition for a completed paid transaction.

## 11. Discovery, satisfaction and channels

### 11.1 Local satisfaction and paths

Business satisfaction conditions include the necessary skills/input-output, the amount ceiling, usable payment methods, acceptable versions, and one's own risk and experience conditions; insufficient samples may be allowed, and newcomers cannot be uniformly excluded.

Reasons to continue discovering include too few candidates, missing samples or reviews, known paths being unusable or not meeting business conditions. Stop reasons must be explainable, distinguishing "enough", "quota exhausted", "sources traversed" and "the owner must decide".

Path optimisation compares only currently verified channels for the same goods: control-port reachability, latency, validity period, necessary task transport capability, privacy and local cost. A control probe does not prove actual task completion speed; referral levels are not execution hops.

A newly discovered better path is used for future tasks. When the original task may already have been delivered, only query that same task; do not change the path and re-execute. No promise is made that every round is shorter, or that the network's shortest path is known.

### 11.2 Every node's bounded public duty

Every online node that joins this protocol network provides handshake, lookup, referral, card fetch, channel resolution and safe probing, and forwards only to an explicit target along a bounded path. With no goods it may return an empty directory; with insufficient capacity it returns an authenticated rate-limited/busy state. Being offline is no promise of service, and a NAT node fulfils its reachable discovery duty through an established outbound mailbox.

Basic discovery reserves capacity, schedules fairly across authenticated counterparties, and has both a global budget and limits against protocol abuse. Payment amounts, goods ratings and commercial relationships do not buy discovery priority. Buyers and sellers blocking each other's goods does not stop normal public discovery; attack traffic can still be rate-limited under the protocol rules.

The public duty is limited to small metadata. Recursively executing an Agent for someone else, holding funds, witnessing or hosting original files are services the owner chooses separately. Continued discovery is controlled by the requester, and a remote side answers only one bounded request at a time and cannot expand the whole network on the requester's behalf without limit.

## 12. SDK and public-network participation

A full installation contains the local platform, identity, private records, listing and buying, samples and feedback, discovery and adapters; having all capability modules does not mean every optional service is automatically open.

After starting on a new computer it automatically checks the same node directory, identity and port; a LAN can be discovered with zero configuration, and on the public network a bounded outbound coordination mailbox is established through a replaceable known public entry. A seed holds no global directory or evaluation authority. With no usable entry it shows isolated and local use continues.

Public discovery and the samples of the first 10 free deliveries stay public and cannot be switched off. Delisting or unmounting only stops taking new orders, and the original sample record remains readable under the original service_id. Task relay, witnessing and large-file caching are chosen by the owner within a quota. A personal node is not forced to execute someone else's tasks or cache large files.

The product completes a unified installer, residency at login, upgrades, backup and restore, network diagnostics, and finished-product input and output. Remote control, automatic use of funds and installing third-party code fall within visible authorisation and cannot gain unlimited rights by virtue of "one-click everything".

## 13. Sample Agents and creator revenue sharing

Listed as a separate later batch: a package declares its purpose, version, publisher, runtime dependencies, permissions, resource limits, external costs, upgrade and rollback; signature verification establishes the publisher's provenance, and a sandbox limits file, network and system access.

Permissions and costs are previewed before installation, and unfamiliar code is not executed without an explicit choice. Agent packages and the transaction protocol are upgraded separately. An installed goods inherits the original template's provenance while holding its own actual service record on this machine, and cannot borrow a template name to pass itself off as a delivery the creator has verified.

Creator revenue sharing is an opt-in contract among participants, not an A2N fee; it depends on real settlement and a verifiable distribution record. Until the sandbox and settlement are complete, no automatic installation execution or automatic revenue-sharing promise is offered.

## 14. Phased delivery

| Batch | Scope | Completion criterion |
|---|---|---|
| B0 Fact specification | Stable transaction key, separated fact axes, compatibility rules | A quality failure does not lose the delivery; replay and cross-channel do not duplicate; historical unknowns are kept honestly |
| B1 Samples and experience exchange | Bad results, self-tests, privacy slots, public signed records, multi-source queries | The buyer gets sourced cases before the call; the seller cannot delete another party's original review from its own machine |
| B2 Reputation observation | Dimension estimates, counterparty contribution caps, information sufficiency, explanations | Adversarial cases pass; the same facts can be recomputed; shadow run by default |
| B3 Local opportunities | Business satisfaction policy, newcomer exposure, acceptance conditions, limitation and recovery | No unauthorised calls are added; rollback is possible; newcomers and reasonable rejections are not systematically excluded |
| B4 Lightweight transactions | Risk quota, fixed quotes, objection replies, one real payment method | The amount is fixed; UNKNOWN does not pay twice; channel receipt and refund facts are checkable |
| B5 Product and public network | New-computer installation, residency, upgrade recovery, multi-network pilot | Two newly installed computers can actually be used for real needs; cross-public-network and restart are measured |
| B6 Later extensions | Reconnect sampling, media completion, sample installation and revenue sharing | Accepted separately; full security is not claimed by borrowing basic installation or a template signature |

Public-network and installation work can proceed in parallel with B1; do not wait for the algorithm to be written before verifying the network. Media can first provide safe thumbnails and placeholders, with file hosting rolled out in batches. Detailed implementation dependencies are in the architecture design.

## 15. Acceptance and policies awaiting calibration

Must cover: 100 self-tests, a fixed pair inflating each other, one person repeatedly rating low, reasonable rejection, network failure, delivered bad results, new versions, new goods with no history, selective provision of positive reviews, expired copies, review revisions, malicious impersonation, trial boundary concurrency, duplicate charge requests, payment UNKNOWN, late receipts, privacy hiding, both sides offline and upgrade recovery.

A real pilot records first-search hit rate, sample viewability, delivery rate, feedback coverage, re-selection ratio, a new Agent's waiting time for its first call, limitation false positives and recovery; after payment begins it also adds settlement ratio and unsettled amount. An arithmetic test upstream does not count as real quality verification.

First-round suggested parameters in summary: 10 initial completions (an established direction); free concurrency default 2, quote validity 60 seconds, freshness half-life 90 days, counterparty weight cap 1, neutral prior 2, exploration display ratio 15%, reconnect sampling 5 calls / 24 hours / lifetime cumulative 30 (including initial). Opportunity thresholds are in §9.4; apart from rules already decided, all may be adjusted through pilots and product-side feedback.

Most in need of calibration: the basis for counting completions versus bad results, the boundary between public and private reviews, dimension weights and support thresholds, when to move from observation to actual opportunity adjustment, the first settlement channel and mode, and whether reconnect sampling is enabled. The implementation must not treat suggested constants as immutable product promises.
