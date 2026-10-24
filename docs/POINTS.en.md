<!-- source: de617e75fa44 -->
# The service points payment module

2026-10-08. Implemented: issuance against a provider's service, voluntary top-ups, debt and repayment, the acceptance table, exchanges of at most three hops, three-party cycles, whole-plan commit and recovery, and a refund of the original points that both sides agree to. The deployment and verification record is in [the delivery record](../artifacts/POINTS-DELIVERY-2026-10-08.md).

## 1. Layers

```text
business: TradeService / CallService / TrialBook / ContractBook / TradeFactsBook
  free sample → direct call, public redacted sample; no settlement order
  ordinary service → explicit settlement intent, fixed terms, actual execution and delivery proof
    ↓
payment coordination: PaymentCoordinator / PaymentBook / PointsCoordinator
  shared capabilities, method selection, fixed plan, reservation, decision, reconciliation and recovery
    ↓ calls each method through a port
native coin EvmNativeDriver | x402 driver | PointsBook / PointsNode
```

`PointsBook` is a zero-dependency SDK module; node injects identity signing and networking. It does not read sample or trial counters, does not decide free eligibility, does not call an agent, and does not touch a native-coin or x402 balance. `PointsNode` only translates the points protocol and signed requests; `PointsPayment` orchestrates the protocol; `TradeService` decides whether to accept an order and execute it.

The old `/v1/payment-coordination/quote`, `free-execute`, `prepare` and `plans/...` remain as compatibility entry points into the same business service and do not form a second set of rules. The console uses `/v1/trades/...`. A sample quote is issued by the business service and stored in `business_free_offers`; it never enters payment coordination's quote, order, reservation or settlement state machine.

## 2. Accounts and entries

An account key is `(issuer_did, holder_did)`. Each issuer maintains only the ledger of points it issued itself; when it holds another issuer's points it obtains a signed balance from that issuer and never adds them into one global balance.

| Mode | Entry after a real delivery | What the consumer pays |
|---|---|---|
| First ten samples | no settlement entry at all | nothing; the redacted sample is published mandatorily |
| EARN | provider points held by the provider increase by the agreed amount | no payment, no debt |
| DEBT | provider points held by the provider increase; the consumer's debt to the provider increases | a debt both sides explicitly accept, capped by the provider's limit |
| PAY | existing points move along the accepted path; no extra provider points are issued | the original points of the chosen issuer, with an integer cost cap |
| Voluntary top-up ISSUE | new provider points are issued to a named consumer | agreed between the two sides off-platform; the platform collects nothing |
| Voluntary top-up TRANSFER | the provider's stock decreases and the consumer's holding of provider points increases | an explicit transfer of existing stock |
| Repayment | provider points held by the consumer go to the provider and the original debt drops by the same amount | no double repayment and no over-offsetting |

Paying with existing points chooses "transfer only" so the service value is not counted twice; a voluntary top-up distinguishes new issuance from a transfer of stock. These are the concrete rules this implementation uses — entries that are not yet decided are not hidden inside a single button.

Per agent, a provider can set whether the mode is enabled, the amount per call, the allowed modes and the debt cap per consumer, and can stop issuing new points. Stopping issuance neither clears existing points nor touches existing trades; a trade that already has everyone's confirmation keeps recovering on its original terms.

## 3. The acceptance table and path finding

The acceptance table is empty by default. A node decides for itself which issuers it accepts, at what ratio, with what per-trade cap, daily receiving cap and maximum holding. The ratio `numerator / denominator` says how much of its own points' value one unit of the counterparty's points is recognised as. An intermediate node exchanges its own points, which it holds, for the other issuer's points it received and recognises.

Path finding allows at most three transfer edges and queries at most 64 explicitly connected points nodes. It first chooses a paying issuer and compares total cost in that issuer's unit; raw amounts from different issuers are never compared directly as a "unified price". Working back from the target service price, each hop is rounded up; equal cost prefers fewer hops, then ordering by node id. With no path, an insufficient balance or a counterparty that does not accept, the reason is stated and no service runs and no points are deducted.

Each plan fixes the issuer, the holder, the amount, the original business task, the input digest, the service version, each party's signature and the route. New prices and acceptance tables apply to new trades; a plan already prepared is not rewritten by new settings. A change in acceptance terms does not automatically change a points amount that already exists.

## 4. Whole-plan commit and recovery

1. The initiating node persists its own signed plan. The participants are the two business sides, each points issuer, and each holder that is debited or credited.
2. Every party confirms the terms first. Any debit of a third party's balance needs that holder's signature; an automatic exchange may only happen within its acceptance table and with sufficient incoming value.
3. Each issuer checks the complete set of confirmations, reserves the original balance or the debt allowance, and persists and signs a prepared credential. A reservation does not change the balance.
4. The business module executes the service trade once and obtains a real delivery credential signed by the provider. An EARN / DEBT issuance must be bound to that delivery. An exchange or repayment trade has no agent execution step.
5. The initiating node first persists a single whole-plan COMMIT / ABORT decision, then sends it to every participant. A COMMIT carries the complete set of prepared credentials plus the service delivery credential.
6. Every party applies the entries idempotently under the same plan id and countersigns a receipt. Once the receipts are complete the state shows CONFIRMED / FAILED; while they are incomplete it stays COMMITTING / ABORTING.

A dropped connection never releases an unknown reservation automatically, and a hop that has completed may not be kept permanently while the other hops are voided. After a restart each node keeps the original plan, preparation, decision and entries. Every 30 seconds a bounded recovery runs in the background: participants ask the initiating node for its decision, and the initiating node resends the original decision. The console can reconcile the original settlement immediately.

When the delivery result is lost, the business recovery interface reads the original private delivery and verifies the original signature, the result digest and the accepted contract, without submitting the agent again. For a request that genuinely was never accepted, the provider first saves a marker forbidding late execution in the same storage transaction, then issues a cancellation credential, and the whole plan is then aborted. While the result is still unknown, it keeps waiting.

A network partition may block completion for a while; the protocol requires every party to obey the signed ledger and the single decision. The value of points is still decided by the other side's recognition of the provider's fulfilment — the system has no network-wide points consensus and no global exchange rate.

## 5. Refunds

A refund for a payment made in existing points uses an existing dispute plan that both sides sign and accept, and returns the issuer points the provider actually received. The points a service was priced in cannot be treated as whatever points actually arrived after several hops, the amount cannot exceed what was received, and the same plan cannot run twice.

A refund is a new, whole-plan committed reverse transfer; it does not rewrite the original delivery or the quality review. EARN has no consumer payment; a DEBT compensation is handled through an explicit provider top-up and consumer repayment, never by fabricating a refund of a payment that never happened.

## 6. Local API

The entry points below keep the management token, Host and Origin protections. Amounts accept decimal integer strings; the ledger uses integers in the smallest unit and the display returns string amounts, so browser precision cannot lose digits.

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/points` | this issuer's balance, reservations, debts, service policy, acceptance table, known nodes and settlement records |
| POST | `/v1/points/configure` | stop issuing, and local experimental transport settings |
| POST | `/v1/points/services` | mode, amount and debt cap for a named agent; requires the policy version |
| POST | `/v1/points/peers` | verify a signed issuer identity and connect an explicit address |
| POST | `/v1/points/acceptance` | the issuer's acceptance terms; requires the policy version |
| POST | `/v1/points/grants` | an ISSUE / TRANSFER voluntary top-up; requires a command id |
| POST | `/v1/points/holdings` | check the consumer's own signed balance with each issuer |
| POST | `/v1/points/swap` | a two-party or three-party signed points exchange; requires a command id |
| POST | `/v1/points/repay` | clear a debt with the original issuer's points; requires a command id |
| POST | `/v1/points/orders/{id}/reconcile` | fill in the original decision and receipts |
| POST | `/v1/points/orders/{id}/cancel` | abort explicitly while nothing has executed or committed |
| POST | `/v1/points/recover-participants` | a participant checks the initiating node's decision |
| POST | `/v1/points/refund`, `refund-reconcile` | execute or reconcile a refund of the original points that both sides accepted |
| POST | `/v1/trades/quote` | business eligibility and service terms; a sample bypasses settlement first |
| POST | `/v1/trades/free-execute` | a free business call |
| POST | `/v1/trades/prepare` | explicitly choose EARN / DEBT / PAY or another payment method |
| POST | `/v1/trades/plans/{id}/execute`, `reconcile` | call or recover the original business, never re-running an agent that already executed |

The points protocol is POST `/public/v1/points/{catalog,balance,consent,prepare,apply,status,decision}`. The catalogue returns only this node's signed capabilities and acceptance terms; a balance is returned only to the holder's own signed request. Every trade operation is bound to its author, target, action, request digest and validity window. The public reverse proxy opens only those explicit endpoints and never the local management API.

The default payment private transport requires HTTPS. A local Docker experiment can be switched to HTTP explicitly by the node owner; that choice changes neither the ledger, nor signatures, nor business rules.

Points can now also be sent over the [private settlement coordination channel](SETTLEMENT-TRANSPORT.en.md). A verified discovery neighbour can automatically check and import an issuer catalogue; the acceptance table and exchange ratios still have to be set on each node. A plan keeps the original participant route, and a lease update does not create a new points plan. Inbound HTTP and the outbound mailbox share the same guards for taking orders and writing the ledger.

## 7. Product and verification

The complete Windows SDK and the Docker image both include the points module and the console. The account page can set modes, do voluntary top-ups, edit the acceptance table, repay debts and run two-party or three-party swaps; the discovery page chooses the payment method among the unified business conditions and shows the issuer used and the total cost cap.

Automation covers ledger entries, signatures and two-sided authorisation, reservations preventing double spending, quota, the original sample bypassing settlement, a real HTTP agent, recovery of the original delivery, recovery of a whole-plan commit, and a refund of the original points. Four actually installed nodes verify the three service modes and a three-hop exchange with a real code agent. Cross-device internet reachability and long-term recognition of points value by real people still need real use to verify.
