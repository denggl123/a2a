<!-- source: d54b3bcc5551 -->
# A2N points and exchange mechanism: design essentials

> A constraint document for collaborators and AI. 【decided】 = the author has explicitly decided; 【proposed】 = raised in discussion and awaiting the author's confirmation.
> Without the author's confirmation, do not treat 【proposed】 as established fact, and do not breach the "red lines".
>
> Rule clarification 2026-10-08: service issuance adds points of the supplier's own held by the supplier; an active grant adds supplier points held by a designated consumer. The first ten samples skip settlement in the business layer; points are an independent payment submodule of payment-coordinated calls. The module has been implemented this round, and the specific interfaces and implementation rules are in [the service points payment module](POINTS.en.md); this document continues to record the distinction between the author's rules and proposals.

## 0. In one sentence

Each agent provider issues its own points. **After a supplier provides a real service, it adds points of its own held by itself according to the issuance rule it publishes**, expressing the value of the service it has produced; a supplier may also actively grant its own points to a consumer. Whether other consumers accept them, and at what ratio, is for them to decide. The purpose is **capability exchange: I use yours, you use mine**. The platform only keeps accounts, matches and displays performance information, and does not set the value of points on anyone's behalf.

### 0.1 Issuer and holder

Points must state both "who issued" and "who holds", and cannot be represented by a single total balance without an issuer.

| Scenario | Points issuer | Account whose balance increases | Business meaning |
|---|---|---|---|
| A provides a real service under the service issuance rule | A | A points held by A | A generates its own points through actual service, which can later be exchanged for services with anyone willing to accept them |
| A actively grants points to B, e.g. an off-platform purchase agreed by both | A | A points held by B | B receives A's points and may use them or exchange them under the other side's acceptance conditions |
| A's Agent delivers the first ten samples | No issuance event | No settlement balance change | Only the business delivery and public sample are recorded; all settlement is skipped |

【decided】 A supplier may configure modes such as "add supplier points after a free call" or "the consumer runs a debt". Whether a consumer debt is also recorded depends on the mode both sides accept; an increase in supplier points must not automatically be interpreted as a consumer liability every time. The specific debit and credit entries are still in section 11.

For example, A provides a service and issues 10 A points as agreed; what increases is **A points held by A itself**. Only when C is willing to accept A points can A exchange them for C's service under C's acceptance conditions; the service happening does not make the acceptance decision on C's behalf.

## 1. Red lines (must not be breached)

1. 【decided】 The platform does not touch money: no custody, no collection on behalf, no deposit or withdrawal entry, no exchange rate.
2. 【decided】 There is no global currency. Points = the issuer's promise about its own service, and their value rests entirely on performance.
3. 【decided】 There are two issuance paths: **a real service triggers the supplier acquiring its own points**, and **a supplier actively grants its own points to a designated consumer**. Each records its source and authorisation separately; the first must not be turned into "the supplier acquires points issued by the consumer". The first ten samples trigger no issuance or any other settlement.
4. 【decided】 Trust is **decided by each counterparty itself** (the acceptance table). The platform sets no grades and no hard threshold for "how long before you can take big orders"; it only supplies conservative defaults and displays the data.
5. 【proposed】 Points can only be exchanged for calls and cannot be withdrawn (reducing prepaid-card and token-type legal risk; a lawyer should be consulted before a formal launch).

## 2. Trials and anchoring

- 【decided】 After an agent is listed, the first **10** services are free, at the seller's cost, and must form a public redacted sample. Purpose: buyer trial + producing real cases and performance data as a price anchor.
- 【decided】 The first ten samples **skip any settlement**: no points deducted, no points added, no payment debt recorded, no exchange, no transfer. The business module determines sample eligibility and directly completes the free acceptance, call, sample and feedback flow; this flow does not enter payment coordination's settlement flow and calls no payment submodule.
- 【decided】 The seller may call itself, **with no separate isolation or marking** (switching identity would bypass it, making isolation pointless). It is identified by **post-hoc deviation**: comparing the scores of samples and cases produced during the trial period with the scores, success rates and performance of later real users; when the deviation is too large, that agent's sample credibility and reputation drop automatically.
- 【decided】 Reconnecting to the network after a disconnection grants another 5 free calls; 【proposed】 only trigger this after a long offline period (e.g. 24h+), to avoid penalising brief disconnections on a home computer.
- 【decided】 A popular agent may close the free entry for new users, after which use requires exchange or an off-platform purchase; 【proposed】 make it a knob (e.g. N seats per day) so newcomers are not shut out entirely.
- 【proposed】 Prices are locked during the trial period, increases are announced in advance, and historical prices are visible to buyers.

### 2.1 Layers and isolation (【decided】)

```text
Business layer: eligibility, first ten samples, supply pricing, accepting orders, Agent calls, delivery and feedback
  ├─ first ten samples: go straight through the free business flow, skipping settlement
  └─ ordinary services needing payment or points bookkeeping: submit an authorised settlement intent
       ↓
Coordination layer / payment coordination: shared capabilities, condition negotiation, plans, method selection, execution orchestration and recovery
       ↓ called through the unified payment port
Independent payment submodules: native coin ｜ x402 ｜ points (issuance, holding, acceptance table, exchange, debt)
```

| Responsibility boundary | Responsible for | Inputs and limits |
|---|---|---|
| Business module | Whether to settle, whether it is a sample, the service price, the actual delivery facts | Stores the reason for being free and the call facts; a sample creates no settlement order and does not replace skipping with "a zero-amount points payment" |
| Payment coordination | Negotiating and selecting a payment method both sides accept, fixing the plan, calling submodules, aggregating and recovering settlement state | Receives business authorisation; does not decide trial eligibility itself and does not execute the Agent on the business's behalf |
| Native coin / x402 submodules | Their own submission, verification, query and refund rules | Do not read sample slots or trial counts, and do not choose the business price themselves |
| Points submodule | This method's issuance rules, per-account ledger, acceptance conditions, exchange, debt and recovery | Service issuance must be bound to the original delivery facts the business provides; an active grant must be bound to supplier authorisation. Does not judge the first ten, does not call the Agent, does not modify other methods' ledgers |

【decided】 "Free but generating supplier points" applies to an ordinary service mode the supplier has opened, and still requires the business to initiate the points bookkeeping; it and the sample flow of "the first ten skip all settlement" are two explicit business conditions. A price of zero alone cannot be used to decide whether payment coordination is bypassed.

Logical isolation is achieved through module interfaces, and may reuse the same node identity, encrypted storage and process. Each method keeps its own bookkeeping facts, the coordination layer keeps the binding between the method and the original plan, and the business layer reads the settlement result for transaction display. Querying or recovering a settlement must not re-execute the Agent, and a repeated notification must not issue, charge or record a debt twice.

### 2.2 Current implementation status

Business eligibility, sample quoting, order acceptance and execution have been split into the node's `TradeService`, while the former `PaymentService` keeps a compatible API and payment channel assembly. Points are implemented by the SDK's `PointsBook`, `PointsCoordinator` and the node's `PointsNode`, `PointsPayment`. Native coin, x402 and points use independent method ledgers, uniformly linked to the original business facts; the first-ten-free flow creates no settlement quote, order or points event. Implementation and verification are in [the points module](POINTS.en.md).

## 3. The acceptance table (the core)

Each person sets it themselves: whose points they accept, the ratio, the holding cap, and per-transaction and daily limits. If the other side has not set it, no exchange is possible.
【proposed】 A new identity starts with a small quota, and the counterparty raises it based on its own record of that identity's performance.

【decided】 The supplier prices its own Agent; the consumer decides which points to accept and at what exchange ratio. A supplier raising prices does not automatically rewrite existing points balances, other nodes' acceptance tables or the conditions of transactions already accepted; later exchanges happen under the conditions each party accepts and fixes at that time.

## 4. Exchange matching

- 【decided】 **Demand driven**: triggered only when "I want to call B's agent but I only hold A"; exchange and call complete in the same step, with no hoarding and no speculative trading.
- 【decided】 **Everyone is a market maker**: routing is centred on oneself, with **at most three hops**; **three-party circular barter** is also supported (A wants B's, B wants C's, C wants A's, each getting what it needs).
- 【decided】 Every hop must fall inside the other side's **passive acceptance table**; nobody is forced to accept points or ratios they have not agreed to. Acceptance conditions express each party's approval, not a guarantee of service quality or absolute fairness.
- 【decided】 Choose the path with **the lowest total cost for the initiator**.
- Pricing rules (every machine must compute the same result):
  - Work backwards from the target, converting each hop by its own acceptance table; the total cost is borne by the initiator, and a ceiling may be set.
  - The products of the per-hop ratios need not be equal; handle the remainder by a uniform rule: the smallest accounting unit, rounded up uniformly (the payer pays the extra).
  - Break ties in order: fewer hops → better performance record → node ID.
- When no path is found, the interface explains why and offers alternatives (buy directly, publish a listing); it must not just show a failure.

## 5. Multi-party settlement and recovery

【decided】 A three-party exchange succeeds **as a whole, as one transaction**. During a network interruption nodes may temporarily be at different stages; after recovery they check the original transaction and correct it. "The first few hops succeeded permanently and the later hops are void" must not be the final result.

The following are implementation constraints. This round adopts a persisted protocol of prepare, a single overall decision, and applying the entries; after everyone prepares, the initiating node must still persist the decision, and then each party verifies and applies it idempotently. "Everyone acked" alone cannot be claimed as completed settlement.

1. All participants accept the same agreement package, fixing the transaction ID, points issuer, holder, each hop's amount, condition version, validity period and signature.
2. Each party first reserves its own quota, persists the original agreement and the prepare record, then signs and returns a confirmation. A reservation does not count as a completed transfer.
3. The same agreement can produce only one verifiable overall decision: commit or abort. A commit credential must prove that all necessary participants accepted the original conditions; all parties finally record the same decision.
4. While no overall commit has formed, related reservations may be released only after an overall abort is confirmed. A mere timeout, a missing response or one node being offline does not prove that other nodes have not formed a commit credential; when it cannot be determined, keep the state as awaiting reconciliation.
5. Once an overall commit has formed, each party idempotently completes its local entries and receipts under the original transaction ID until all reconciliations agree. A lost response, a node restart or the last hop going offline must not turn the whole transaction into a partial settlement, nor create another transaction and charge again.
6. 【proposed】 Adopt persisted stages of prepare, overall decision, applying entries and reconciliation, and limit the number of concurrently pending reservations and the protocol validity period; the specific decision credential, disconnection recovery and abort proof belong in the points module's protocol design.

**Guarantee target**: final accounts agree, each party settles under the conditions it accepted, and the original transaction is never charged twice. During a network partition one may wait for recovery, but immediate completion by all nodes cannot be promised at the same time. Payment outcome and service quality are recorded separately; the points module does not adjudicate whether a service is "absolutely fair".

## 6. Reputation and anti-gaming

- 【decided】 Survival of the fittest: multi-dimensional metrics (success rate, latency, network parameters, deviation from samples, repeat purchases), with bilaterally recorded reviews; a past user's low rating is not erased by score inflation.
- 【decided】 Discovery scope is "the part I need", not the whole market, and there is no global jackpot, so the payoff from gaming is low; **no dedicated anti-gaming mechanism for now**.
- 【decided】 Sock puppets are not a primary problem (they actually show the service has value); "return rate" is a more reliable signal than "number of new identities".
- 【decided】 **Deviation between samples and real use** is the core signal: the bigger the gap between sample scores and later real-user scores, the more suspicious. 【proposed】 Deviation needs enough real samples to take effect (early on, show only the sample count without concluding); smooth small samples.
- 【proposed】 When displaying reviews, distinguish paid from trial-period ones, and show the number of independent reviewers.

## 7. Real money (outside the platform)

- 【decided】 A seller may set a **reference unit price** for its points; the buyer pays off-platform and the seller adds points manually. The platform does not handle money, only warns of risk and notes that "settlement through the market is also possible".
- 【decided】 The role of real money is to give points a **public ruler**, preventing value distortion when each issuer's points are compared against each other. Someone who bought too many can pay them out to others (provided the other side is willing to accept).
- 【decided】 An issuer may stop issuing at any time (after which its points can only be obtained by purchase or exchange); holders bear their own risk.
- 【proposed】 The interface shows no "top up" button and no fiat transaction price field; use wording such as "reference price" and "contact the issuer to buy"; state clearly that "money transfers are not protected by the platform".

## 8. Information to display

Listed price vs the market's actual transaction ratio (the gap = a credit signal); performance record (delivery count, success rate, latency, online time); 【proposed】 the number of "sent but unused" points against average daily capacity.

## 9. Where it is easy to go wrong (attention, AI)

- ❌ It is not a token or an exchange: no global coin, no hoarding, no speculation.
- ❌ It is not platform pricing or platform-issued quota: trust quotas are given by the counterparty itself.
- ❌ It is not a "top up and buy" product: the platform has no payment entry.
- ❌ It is not a market maker earning a spread: in a three-party ring each party gets what it needs.
- ❌ Do not design a complex anti-gaming or anti-Sybil system, or a global leaderboard.
- ❌ Do not implement multi-currency multi-hop automatic exchange right away (see the phases).

## 10. Phases (【proposed】)

1. **Phase one**: trials + single-seller quota + bilateral bookkeeping + direct two-party exchange (mutual debts cancel out automatically).
2. **Phase two**: acceptance table + single-hop exchange.
3. **Phase three**: three-hop routing, three-party rings, reference price and transaction price display.

## 11. Open questions

The implementation rules adopted this round: an existing points payment only transfers a balance; an active grant explicitly chooses new issuance or transfer from existing holdings; a free points increase records no consumer debt, while DEBT mode records an authorised debt separately under the supplier's cap; the acceptance table is empty by default; stopping issuance keeps existing points and accepted transactions. The overall commit and disconnection recovery are in [the implementation protocol](POINTS.en.md). These choices stay explicit, and later changes need new rules and compatibility handling.

Still open for discussion: whether to allow a standalone transfer detached from a service; whether to add a new mode that issues while paying with existing points; how points holders negotiate during a long service outage; whether to offer suggested acceptance-table values for new identities. There is currently no network-wide adjudicator, and quality objections continue to use bilateral negotiation and the reputation mechanism.

These details cannot, by assumption, change the issuance target confirmed in section 0, nor delegate the sample eligibility of section 2 to the points module.

## 12. Priority reminder

**Points issuance and exchange have entered implementation and local multi-node verification**, while native coin and x402 keep private local chain verification. The core assumption about real users still needs verification: find 5–10 people, each listing 1–2 genuinely usable agents, start with direct exchange, see whether they call each other and come back for more, and only then decide how to grow the exchange network.
