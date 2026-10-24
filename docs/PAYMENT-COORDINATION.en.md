<!-- source: a7e55840e487 -->
# The payment coordination layer (native coin, x402 and points implemented, 2026-10-08)

2026-10-09: added the provider support list, the consumer's payment order and quota-limited automatic settlement. The detailed rules are in [automatic matching and settlement between peer accounts](AUTOMATIC-SETTLEMENT.en.md). The per-trade confirmation flow on this page still works; automatic mode is authorised by explicit rules the owner saved in advance.

## Business purpose and layers

Every complete SDK node negotiates payment terms, pays and reconciles the facts on its own. The platform commission is always zero; with native coin and x402 the funds go straight from the buyer's wallet to the seller's wallet. Public discovery collaboration remains a node obligation; the wallet, payment quota and receiving channels are configured by the node owner. Points are wired in as a separate payment submodule, and their issuance, holding, debt, voluntary exchange and recovery rules are in [the points module](POINTS.en.md). A points trade needs no wallet configured.

[Responsibility boundary confirmed on 2026-10-08] Payment coordination belongs to the **coordination layer**. Network coordination handles discovering identities, supply and paths; payment coordination takes the settlement intent the business layer submits, chooses a payment capability both sides accept, and orchestrates settlement. Native coin, x402 and points each execute their own rules through a separate payment submodule. Payment coordination changes no discovery algorithm and forms no central funding ledger.

The business layer decides whether settlement is needed. The first ten samples go straight through the free business flow, skipping payment coordination's settlement flow and every payment submodule, and create no zero-amount payment order. An ordinary service that is "free but generates provider points" still writes a points entry and must have a settlement intent raised by the business layer. A payment submodule does not read sample counters, does not decide trial eligibility and does not execute an agent.

**Current implementation**: the node's `TradeService` independently carries business quoting, free eligibility, accepting an order and calling the agent. `PaymentService` handles native-coin and x402 interaction and channel orchestration, and its old business entry points delegate to `TradeService`; points are orchestrated by `PointsPayment`, while `PointsNode` and `PointsBook` only execute the points protocol and the ledger. Free business terms never reach the payment quoter and create no points or other payment order.

| Layer | Implemented modules | Responsibility |
|---|---|---|
| SDK coordination | `payment_coordination.py` | bilaterally signed capabilities, enquiry, quote, plan, acceptance credential; intersection of public terms; fixed input and offering version |
| SDK coordination | `payments.py` | driver registry, one order per trade, several explicit attempts, risk reservation, definite failure, unknown lock, recovery and reconciliation |
| SDK business | `contracts.py` | free and paid acceptance contracts, buyer authorisation, seller signature, delivery contract verification |
| SDK business | `trade_facts.py` | records execution, quality, payment, refund and dispute separately; payment reconciliation never triggers a re-execution |
| Node business adapter | `trade_service.py` | business quoting, settlement bypass for samples, accepting an order and agent delivery, recovery of the original delivery, the points refund protocol |
| Node payment coordination adapter | `payment_service.py`, `points_payment.py` | configuration, bilateral HTTP interaction, actual channel orchestration, settlement, refund and recovery |
| SDK points coordination | `points_coordination.py` | routing in a fixed issued currency, bilaterally or multi-party signed intent, reservation across all parties, a single durable decision, idempotent recovery |
| Separate points submodule | SDK `points.py`, Node `points_service.py` | own issuance ledger, holder balances, debt, acceptance ratios and limits, the signed protocol, reservation and commit |
| Node adapter | `payment_channels.py` | stores immutable channel versions encrypted; the original plan, payment, refund and restart recovery keep using the original wallet and channel |
| Separate payment submodule / Node adapter | `evm_payment.py` | EIP-1559 native-coin direct transfer, transaction persistence, a single broadcast, independent receipt verification |
| Separate payment submodule / Node adapter | `x402/` | x402 v2 exact/EVM/EIP-3009 authorisation flow; settlement service and independent chain verification; a separate reverse-refund driver |
| Local console | `web/payment.js` | import a wallet, configure channels, set a quota, quote, accept terms, pay explicitly, reconcile the original payment, call, execute a refund |

The same encrypted `runtime.db` and node identity are reused; logical isolation does not require another process or database. The business layer stores the facts of calls, trials and samples; payment coordination fixes the method and the original plan; each submodule stores the settlement facts of its own method. The SDK adds no third-party dependency; chain signing and network access stay in the node adapter layer.

## Bilateral trade rules

1. The buyer's signed enquiry carries both DIDs, the offering, the skill, the task number, the canonical input digest, the configured capabilities and a proof of wallet ownership.
2. The seller's business service checks its own real free eligibility and price book; when settlement is needed it then queries the payment channels actually available and returns a signed quote. The first 10 technical deliveries stay free and are mandatorily published as redacted samples, skipping any settlement; a quality review does not affect the count. Configuring a wallet or a points policy does not end the trial early. Both the free quote and its execution are done by `TradeService`.
3. In this version a paid quote supports only a declared fixed single-call price with `call_count / per=1`. An unpriced service, no shared channel, or metered charging has no executable quote, and cannot be defaulted to free or silently switched to another currency.
4. A negotiable option must lie in the intersection of both sides' methods, chains, assets, currencies and payment ordering. An x402 receipt also needs explicit support from the settlement service's `/supported`.
5. The buyer explicitly chooses the option and the network fee cap. The plan fixes the quote, amount, currency, chain, asset, payee, payer, skill, offering version, input digest, ordering and the zero platform fee. Both node signatures are bound to the proof of wallet ownership.
6. When the seller's business service accepts, it freezes the accepted-order eligibility; if the call should be free at that point, it rejects the paid acceptance and requires the free business terms. The buyer's machine reserves funds within the owner's quota and then produces a READY payment attempt.
7. Accepting a plan alone pays nothing. In per-trade mode the buyer pays explicitly; in automatic mode the authorised automatic call entry point pays according to the fixed plan. For a direct transfer the seller independently verifies the original transaction before accepting the paid call. For an x402 call the authorisation is signed at call time, and the seller verifies the signature and the authorisation first, then executes the agent once and settles.
8. One trade UID has exactly one payment order; the order contains the historical attempts. Each attempt is persisted separately. An unknown, submitting, querying or confirmed attempt cannot change channel or sign another payment.
9. A READY attempt can be cancelled explicitly. Cancelling first fixes the local result, then sends the buyer's signed revocation and obtains the seller's signed acknowledgement. A plan from before the revocation can no longer authorise a call; a new plan still needs both sides to accept.
10. When the response to an accept or revoke is lost, the same signed record can be sent again; no new payment authorisation is invented. Only a definite failure allows a new attempt. Querying the original channel is the only recovery direction for UNKNOWN.

The offering version, skill and input of one order can never change. When the original task has been delivered and the original payment definitely failed, a top-up by direct transfer can be negotiated, reading the original delivery; the agent is never executed again. While the execution result is still unknown, the original delivery must be recovered first, and the task may not be restarted by switching payment method. A free original trade cannot be turned into a paid one after the fact.

An enquiry and a quote are valid for at most 300 seconds. Signatures and the fixed plan are checked again at execution. A coordination call accepts at most 64 KiB of input; the public payment entry point accepts at most 256 KiB per request; the original x402 protocol response is capped at 1 MiB. Each node holds at most 512 valid quotes at once, and 16 per buyer. Ordinary A2A capabilities keep their own limits.

An original plan the seller already accepted can recover the same signed acknowledgement after the confirmation response is lost, even if the quote has since expired; an expired plan cannot take a new payment and must be cancelled and re-quoted. The console shows plans whose confirmation was interrupted, payment orders, and the terms the seller received. A public enquiry carries only the input digest and may use an existing node's HTTP discovery route; payment authorisation, full input and private pickup credentials require HTTPS by default, and HTTP is allowed only when the owner explicitly enables local testing.

### Channel settings and original orders

A settings change creates a new channel version, and new quotes use the new settings. An unpaid acceptance credential does not lock the receiving settings indefinitely; when there is payment exposure, an unknown payment or a confirmed payment, its original channel is kept for reconciliation and refund. The original wallet, chain, asset, RPC, secure transport settings and driver version bound to an order are never replaced by a settings change. Old wallet keys stay in the local encrypted ledger, and the API never returns a key.

A seller's acceptance credential carries its own authorisation state: OPEN, EXPIRED, EXPOSED, REVOKED, FULFILLED and so on. Computer time running out only forbids new payment authorisations; it does not prove the original payment failed, does not release unknown funding risk, and does not prevent verifying a late original payment. Valid and exposed acceptance credentials are subject to the per-node limit of 512 and the per-buyer limit of 16.

Orders that existed before this upgrade bind the channel version that was still obtainable at upgrade time; old configuration that another program had already overwritten and never saved cannot be recovered. A preserved original channel keeps reconciling the original order across wallet rotation, closing the current channel and a node restart. Removing a current capability does not automatically clean up the original channel or wallet.

## Channel execution and recovery

### Native coin direct transfer

- EVM EIP-1559, with the network as CAIP-2 `eip155:<chainId>`; amounts and network fees are integers in the smallest unit of that native coin.
- Only EOA receiving addresses are accepted, so an ordinary payment cannot accidentally execute a contract. The transaction calldata binds the plan digest, so somebody else's transfer to the same address cannot stand in for this payment.
- Gas is actually estimated, capped at 200000 gas, and the explicitly approved maximum total network fee is obeyed. The amount and the fee cap consume the budget together.
- The signing sequence number for one wallet is allocated serially inside an encrypted-ledger transaction, and the same sequence reservation is shared even when an old order coexists with a new channel version; the complete signed transaction, hash, nonce and plan binding are stored encrypted first, then broadcast once.
- A broadcast timeout or an RPC rejection does not prove the transaction did not happen. UNKNOWN is kept and only the original hash is queried; a restart does not broadcast again either.
- It verifies the chain id, transaction hash, payer, payee, amount, plan calldata, canonical block hash, receipt and confirmation depth. On success the spend is recorded at the actual network fee; on a definite failure that still burned gas, the actual gas spend is recorded too.
- An external wallet operating the same address can cause a nonce conflict or a wait. The node never automatically creates a new payment or raises the fee to resolve the conflict.

### x402

- The supported scope is v2, exact, EVM, EIP-3009, EOA, authorisation flow; Permit2, Solana, escrow and other orderings are not passed off as supported.
- The payment signature is persisted before it is exposed; one plan fixes a single authorisation nonce. A repeat request with a different nonce is rejected by the coordination layer even if it carries a valid signature.
- `/verify` and `/settle` adapt the standard service, but whether funds arrived is decided by an independent EVM RPC's events, receipt, canonical block and confirmation depth.
- By default the buyer's network fee is zero; the actual chain gas is paid by whoever submits to the settlement service, and any other charge by that service must be agreed separately — it is not the platform being free.
- When the settlement response is lost after delivery executed, both sides keep the payment and delivery facts. The buyer asks for recovery with its own node signature and the original private pickup credential; the seller only checks the chain and reads its cache, and never re-runs the agent or settles again.
- The recovered result goes through the bilateral receipt and the accepted-order contract again, and is written back to the original call. A delivered call whose payment is still UNKNOWN can be shown separately.
- An expired authorisation may only be judged a definite failure when the block time, a complete event scan and the unused state all confirm it. Computer time running out alone does not release risk quota.

On-chain confirmation depends on the RPC the owner configures. What the node verifies is the canonical chain fact that the chain service provides; this version is not an independent full-node consensus client.

## Refunds

A refund requires the original trade, the original payment confirmation, a REFUND plan that both sides signed and that is BOTH_ACCEPTED, and explicit execution by the seller. The refund amount, currency and both wallets are derived from the original payment and the signed plan, and cannot be replaced arbitrarily by request parameters.

A refund is a new order bound to the original trade and the plan digest. Confirmed or undetermined refunds together may not exceed the original payment. A repeated plan uses the original order; an unknown refund only reconciles the original channel.

- Native-coin payment: a separate EIP-1559 reverse transfer, with the seller approving the network fee budget separately.
- x402 payment: a separate EIP-3009 reverse authorisation, with the token transfer completed by the settlement service the seller configured; no agent is executed again. The seller's token allowance must be configured in advance.
- Points PAY: a reverse transfer of the issuer points the original order finally received; the bilaterally signed plan binds the original trade and currency, and cumulative refunds may not exceed the points actually received. EARN has no original payment to refund, and a DEBT compensation is done through a voluntary top-up and a repayment.
- After receiving the seller's signed notice, the buyer independently verifies the reverse transaction or authorisation event. A failed notice can be reconciled again against the original refund; no money is paid twice.

Payment, refund, delivery and quality review do not overwrite one another. A partial refund only means that refund plan was paid; it does not mean the whole original order was returned, and it does not automatically invalidate the quality review.

## API

Every local interface requires local management authentication; wallet and budget settings are not offered on the public entry point. A wallet file passphrase is used to import an encrypted keystore, and the key is written only into the existing encrypted ledger; the API never returns a key. Neither a default wallet nor a quota is configured.

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/payment-coordination` | channels, orders, attempts, wallet addresses and refund summaries |
| POST | `/v1/payment-coordination/configure` | configure native/x402, an optional encrypted keystore and passphrase |
| POST | `/v1/payment-coordination/budget` | an exact integer quota, with a configuration-revision CAS |
| POST | `/v1/payment-coordination/quote` | `{projection_id, request}`, request a quote for a fixed input |
| POST | `/v1/payment-coordination/prepare` | `{offer_id, option_index, fee_cap_minor, command_id}`, accept explicitly and reserve |
| POST | `/v1/payment-coordination/free-execute` | `{offer_id}`, call under the free terms |
| POST | `/v1/payment-coordination/plans/{id}/accept` | recover the same acceptance interaction |
| POST | `/v1/payment-coordination/plans/{id}/pay` | an explicit native-coin transfer |
| POST | `/v1/payment-coordination/plans/{id}/execute` | the paid call, or an explicit x402 authorisation and call |
| POST | `/v1/payment-coordination/plans/{id}/reconcile` | reconcile the original payment and original delivery |
| POST | `/v1/payment-coordination/plans/{id}/cancel` | cancel before submission, with a signed revocation |
| POST | `/v1/payment-coordination/refund` | `{proposal_id, fee_cap_minor}`, an explicit refund |
| POST | `/v1/payment-coordination/refund-reconcile` | `{proposal_id}`, reconcile the original refund |
| POST | `/v1/trades/{quote,free-execute,prepare}` | general business quoting, a free call, an explicit choice of settlement method; the console uses these |
| POST | `/v1/trades/plans/{id}/{execute,reconcile,cancel,pay}` | act on the original plan; points and other methods are dispatched by the fixed method |
| GET / POST | `/v1/points`, `/v1/points/*` | separate points accounts, policy, top-ups, exchanges, repayments and reconciliation; see the points module interface table |
| POST | `/public/v1/trades/{quote,points-recover}` | a signed business enquiry, and recovery of an original points service delivery |
| POST | `/public/v1/points/{catalog,balance,consent,prepare,apply,status,decision}` | the signed points protocol; management settings are not public |
| POST | `/public/v1/payments/{quote,accept,revoke,observe,execute,recover,refund-observe}` | the bilateral signed protocol, or the x402 resource entry point |

Example configuration (the wallet is imported separately through an encrypted keystore; fill in the example addresses yourself):

```json
{
  "config": {
    "native": [{"currency":"ETH","network":"eip155:84532","rpc_url":"https://your-rpc","confirmations":3}],
    "x402": {"assets":[{"currency":"USDC","network":"eip155:84532","asset":"0x-the-real-token-contract","name":"the-real-eip712-name","version":"2","rpc_url":"https://your-rpc","confirmations":3}]},
    "facilitator_url":"https://your-settlement-service"
  }
}
```

The RPC, token contract and EIP-712 domain must really match. Without the corresponding configuration it says "not configured" and never shows the method as payable.

## Verification and the boundary for later extensions

Native coin, x402 and points are wired into the [separate encrypted settlement coordination channel](SETTLEMENT-TRANSPORT.en.md): when the provider and the consumer have only outbound connectivity, they can still negotiate on the original plan, reconcile delivery and send a refund back. The relay only forwards ciphertext; chain RPC, facilitator and each submodule's authorisation rules keep their original boundary.

Tests cover a real private EVM native-coin transaction, a lost broadcast response, reconciliation after restart, a paid call between two nodes, the first 10 free samples, cancelling and choosing again, an independent refund, and x402 bilateral receipts with cached delivery recovery. Test coins have no real value and no mainnet funds are used.

The x402 integration check deploys a test EIP-3009 contract, submits an on-chain transaction through a real HTTP settlement service, and verifies the payment and the reverse refund against both parties' token balances. A refund amount larger than the JavaScript safe integer range is verified through local HTTP, the signature exchange and exact text display; the signed protocol still uses integer amounts. The production image does not contain the private-chain test dependency.

To reproduce the private-chain check, first install the project and `requirements-test-payment.txt`, then run `pytest tests/test_payment_coordination.py tests/test_paid_market.py tests/test_payment_channel_lifecycle.py tests/test_resolutions.py`. After building the `a2n-node-test` image you can also run `python scripts/check_payment_docker.py`; that script installs the test dependencies only into a temporary check container, the ledger and wallets are isolated from the existing four nodes, and the container is removed when it exits.

Points now also cover issuance against a provider's real service, voluntary top-ups, debt and repayment, two- to three-party cyclic exchange, a voluntary acceptance path of at most three hops, recovery of the original decision after a disconnect, and an independent refund. A new method joins the same coordination boundary and trade facts only after it implements injected ports, a real capability declaration, exact observation and definite failure conditions. Fiat, other chains, escrow, metering after payment, on-chain multi-hop payment on behalf and creator revenue splits are not implemented; points exchange capability does not mean those payment methods are supported.

Standards: [Ethereum JSON-RPC](https://ethereum.org/developers/docs/apis/json-rpc/), [EIP-1559](https://eips.ethereum.org/EIPS/eip-1559), [EIP-3009](https://eips.ethereum.org/EIPS/eip-3009), [x402 v2](https://github.com/x402-foundation/x402/blob/main/specs/x402-specification-v2.md).
