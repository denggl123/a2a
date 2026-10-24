<!-- source: 86509bede6f0 -->
# Automatic matching and settlement between peer accounts

## 1. Participants and configuration

No central platform holds a collecting account, and the platform fee is zero. The provider declares which settlement methods it accepts; the consumer configures the methods, their priority order and the quota on its own node. Only a method both sides support can become a term of this trade. A provider's points account is its node DID; an on-chain account is determined by each side's own wallet and channel configuration.

In the console, "My account → Settlement methods and order" lets you add, remove and reorder methods. Points, x402 and direct transfer enter their own submodules through the same payment coordination entry point. Creating a new settlement rule does not migrate or reprice existing trades.

Configuration fields:

| Field | Meaning |
|---|---|
| `provider_methods` | the provider's supported list; the agent's own points mode, actual wallet and channel must still be usable |
| `buyer_preferences` | the consumer's payment rules, earlier entries taking priority |
| `automatic` | whether to authorise automatic execution of a call that already has a quote; off by default |
| `expected_revision` | an update must match the current revision, so a concurrent configuration change is not overwritten |

`null` keeps the existing channels and preserves per-trade choice; `[]` says explicitly that no paid method is supported. Free services and the first ten samples can still run. Automatic settlement must supply a non-empty consumer rule with an explicit amount cap, and for points it must also name EARN, DEBT or PAY.

## 2. Matching rules

1. The business layer first fixes this request and whether it is free. The first ten free technical deliveries always become a redacted public sample; at that stage no payment plan, points issuance or points debt is created.
2. The provider issues a signed quote based on its service pricing, supported list and actual channels. The consumer's capabilities and the provider's capabilities are intersected.
3. Walk the quotes in the order of the consumer's rules. `method` is required; any `network / asset / currency / flow / mode` that is filled in must match exactly. Fields left empty may match whatever that method actually offers. Different chains, tokens or currencies are never treated as the same, and no automatic conversion of fiat or on-chain assets happens.
4. Check this rule's per-trade amount cap. If it is exceeded, check the next rule; the first option that fits becomes the recommendation. Matching a quote neither pays nor executes an agent.
5. An automatic call stores the option chosen first, the command id and the contract; after accepting the original quote it runs the corresponding submodule. On-chain risk control separately checks per-trade, daily, cumulative-unsettled and same-counterparty limits. The points submodule checks balance, debt permission, exchange relationships, path and each party's reservation.

Rule amounts are non-negative integers in the smallest unit, submitted as decimal strings so browser integer precision cannot lose digits. `max_amount_minor` caps the quoted amount; for a native transfer `fee_cap_minor` caps the network fee separately, and the risk-control total includes that fee. A points PAY rule may pin `funding_issuer` and `max_cost`; with no max_cost the quote amount of this trade becomes the spending cap, so a cross-issuer exchange cannot spend without bound.

Example: first accept an EARN service where the provider adds its own points, then try USDC on a given network:

```json
{
  "expected_revision": 0,
  "provider_methods": [{"method":"a2n-points/1","mode":"EARN"}],
  "buyer_preferences": [
    {"method":"a2n-points/1","mode":"EARN","max_amount_minor":"100"},
    {"method":"x402/2","network":"eip155:8453","currency":"USDC","max_amount_minor":"1000000"}
  ],
  "automatic": true
}
```

## 3. Automatic acceptance and payment ordering

Three kinds of acceptance must be kept apart: release acceptance verifies the project version and product; the task's declared template checks the agreed structure or rules; the quality review checks the quality of the actual task. None of the three substitutes for another. A call with no declared template only says a delivery arrived, sets `quality_measured=false`, and makes no claim that the quality is acceptable.

| Method | Current ordering |
|---|---|
| Samples and other free terms | execute, deliver technically, apply the acceptance that fits, record the experience; settlement is skipped |
| Native transfer | accept the original quote → confirm payment → call the agent and accept |
| x402 exact | accept the contract → fix the authorisation → execute and record the delivery fact → settle/reconcile against that authorisation |
| Points | accept the contract and each party's reservation → execute → provider signs the delivery → decide and apply for each party → converge |

Existing automatic settlement rests on the agreed technical delivery and payment protocol; a human review or a semantic quality failure does not automatically reverse a payment that already happened. Quality disputes are recorded separately, and a refund still needs a plan both sides accept. Points EARN/DEBT means the provider adds points it issued against a real service; PAY spends points it holds; a three-party exchange converges through a recoverable protocol, and a network interruption does not mean everything committed physically at the same moment.

## 4. Interruption, failure and changing the authorisation

- The same `command_id + offer_id` returns or recovers the same plan; reusing a command with different parameters is rejected.
- A failure while preparing the chosen plan keeps the original command. An unknown payment, authorisation, delivery or points commit only reconciles the original plan and never switches to the next method to create a second payment or execute the agent again.
- No shared method, an insufficient quota, a missing wallet or an unusable points path stops the call explicitly. A failed chosen plan is never silently followed by another method; every later change must deal with the original order explicitly.
- Turning automatic settlement off, or tightening the consumer's supported methods and quota, blocks automatic payment or points calls that have not started. An original order that already had an external effect can still be recovered; the frozen account and channels are used to reconcile it.
- `NodeClient.call/call_agent` reads the local authorisation by default; with automatic settlement on, an imported signed node agent is quoted, chosen and executed automatically. With it off, the original per-trade path applies. Locally mounted agents, unsigned external agents and original orders that already have a payment plan keep going through the single call chain.
- Use a stable `task_id`. An SDK retry first reads the persisted call record and reuses the original quote and plan; reusing the same task id with different input is rejected. After UNKNOWN, `get_task` looks up the original order, so there is no need to start a purchase again.
- Each background round recovers one authorised unfinished order and records the backoff time and diagnostics; recovery continues after a node restart. A historical free record with no quote is never executed on a guess. The background never discovers or buys a new agent.
- For a quote from a signed node, when a free call or a native-coin payment loses the delivery response, the original quote yields a definite remote task id, and only the original task and original receipt are queried. When an ordinary external agent's id is unknown, no guess is made and the call is not re-sent. If the original provider has no verifiable record, the result stays undecided and a successful delivery is never fabricated.
- When querying the original task runs into an unauthorised error, a task not found, a protocol error or an invalid state, `DELIVERY_UNKNOWN` is kept and a failed observation is not counted as a failed agent execution. Only an explicit failure, rejection or cancellation from a valid original task is recorded as that terminal state. Query records that an older version wrongly marked failed are restored to undecided, keeping the original payment and continuing read-only queries.

## 5. Boundaries and interfaces

The SDK's `SettlementPolicy` holds only local rules and pure matching; `PaymentCoordinator` holds the dual-signed contract and the unified order; the node's `AutomaticTrade` orchestrates one authorised call; the native-coin, x402 and points modules own their accounts, protocols and settlement. Discovery and scoring never make a payment.

Local interfaces (protected by the existing management authorisation):

- `GET /v1/payment-coordination`: rules, orders and the state of configured channels.
- `POST /v1/payment-coordination/policy`: a complete rule update.
- `POST /v1/trades/quote`: get a quote for one fixed input.
- `POST /v1/trades/match {offer_id}`: pure matching plus the reasons for exclusion.
- `POST /v1/trades/auto-execute {offer_id, command_id}`: fix the choice, execute or recover.
- `POST /v1/trades/call {scope, request, automatic}`: the SDK's ordinary call entry point, returning a canonical CallOutcome; `automatic` may be omitted, or set to turn automatic quoting off for this call. An existing automatic order still recovers as that order.
- `GET /v1/trades/call?scope=...&task_id=...`: look up the original automatic call; returns null when it does not exist, and the SDK then queries the original A2A task.
- `GET /v1/trades/automatic`: the state, original plan id, backoff and diagnostics of the last hundred tasks; `summary` counts all orders, the unfinished ones, the ones needing attention and the quotes that have no order yet, and returns no call input or wallet credential. A truncated list does not mean older original orders are finished.
- `GET /v1/trades/automatic?command_id=...`: read that order's state and the recorded delivery; the console polls this read-only to show background recovery and never re-executes anything.

The single existing interface still supports per-trade confirmation. Parallel routing across several wallets, interoperability of every payment protocol, escrow adjudication and a mainnet funding security audit are outside what has been verified here.
