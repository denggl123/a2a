<!-- source: f9645259d5e7 -->
# Private settlement coordination channel

## 1. Boundaries

`a2n-settlement/1` only transports protocol messages of the payment submodule; it does not do points entries, on-chain signing or business execution decisions. The business still checks the initial ten free samples first, and a free business does not create a settlement plan.

The six coordination discovery operations keep their original interface. Settlement, experience, negotiation and files each use their own protocol, encryption domain, inbox key, queue and budget. The platform commission is still zero.

## 2. Addressing and joining

The supply card signature covers `x-a2n.settlement = {protocol, route}`. The route is an explicit direct source address, or `{kind: "settlement_mailbox", endpoint, relay_did}`. Only nodes with outbound connections register a short lease with a public node of their choosing; the relay holds the ciphertext temporarily and the target node collects it actively.

When requesting a quote, both sides sign a binding to the buyer's return route. An accepted plan stores the original logical route; continuing payment, revocation, original delivery recovery and refund return use the original plan, without creating a new transaction and without switching relays automatically. Before each mailbox send, fetch the target's new lease and verify the target, relay, address, validity period and encryption public key; do not pin a key that is about to expire into the plan.

The first-use entry points GET `/v1/onboarding` and POST `/v1/onboarding/connect` are protected local management interfaces. A connection must first complete a signed HELLO; a failure is not persisted as connected, and a success saves the public node and outbound configuration. If the peer declares a sealed task relay, connect to that task channel. Configuration success and lease establishment are shown separately.

## 3. Public interfaces and permitted messages

All are POST; the public gateway opens only the explicit paths below and does not forward the local management API.

| Path | Purpose |
|---|---|
| `/public/v1/settlement/capabilities` | Signed request for the target's settlement protocol and public key |
| `/public/v1/settlement/dispatch` | Encrypted message delivery to the target submodule |
| `/public/v1/settlement-mailbox/{capabilities,register,lookup,poll,reply,forward}` | Standalone outbound settlement mailbox |

The ciphertext binds the author, target, request ID, method, operation, message body and reply public key. Inner and outer authors must agree, and the submodule continues to verify its own signature, target, original transaction, validity period and authorisation.

| Method | Permitted operations |
|---|---|
| points | catalog, balance, decision, consent, prepare, apply, status |
| trades | quote, points-recover |
| payments | accept, observe, execute, recover, revoke, refund-observe |

x402's original `PAYMENT-SIGNATURE`, idempotency identifier and resource request body are forwarded inside the ciphertext. HTTP status, payment response headers and the original response body are returned unchanged. Chain RPC and the facilitator keep using the original payment channel; this channel has not created a new payment method or on-chain multi-hop capability.

## 4. Encryption and quotas

Uses X25519, HKDF and AES-GCM; domain `a2n-private-settlement/1`, with a persisted standalone inbox key. A relay can see node identity, timing, size and route, but not business inputs, balances, authorisations or delivery bodies.

Each plaintext is at most 90,000 bytes and each outer envelope at most 196,608 bytes. A mailbox holds at most 32 leases, 2 per target and 8 in-flight requests globally, with 512 forwards per minute and 120 per author. A lease lasts 90 seconds, the relay waits 18 seconds and a request waits 20 seconds. Large files continue to be read through the existing encrypted file module under the original transaction authorisation.

The old direct entry keeps HTTPS, or local HTTP explicitly allowed by the owner. The encrypted coordination channel keeps the fixed-DNS, signed-identity and trusted-network access constraints; one referral cannot grant permission to access arbitrary internal addresses.

## 5. Unknown outcomes and recovery

After DISPATCH starts being sent, a timeout, an unverifiable response or an indeterminate service error all return UNKNOWN. This must not be used to switch channels, create a new authorisation or re-run the Agent. The user and the backend reconcile against the original plan, original authorisation, original delivery and each party's sole decision.

Points pass the same guard for acceptance eligibility and entries, consistently across the HTTP, local-node and mailbox entry points. A points catalogue may be imported from a signature-verified coordination neighbour, and the issuer's catalogue is read and verified first; import does not change acceptance ratios, balances, wallets or quotas.

Refunds follow the original bilateral agreement and a separate reverse payment. Payment confirmation and the counterparty receiving the refund notice are two facts; if the notice is lost, the original refund can be reconciled, but a second refund transfer must not be produced.

## 6. Verification boundaries

`tests/test_settlement_transport.py` uses supplier and buyer declared addresses with no listener, a real Daemon, the outbound mailbox and sealed A2A to verify the initial sample, points issuance and arrears, original delivery recovery, catalogue bridging and rejection of forgery; a private EVM verifies native currency, real x402 transactions and reverse refunds. The test coins have no real-world value.

Same-machine simulation verifies the protocol business; real multi-device NAT, carrier firewalls, long outages and public-network performance still need an external environment.
