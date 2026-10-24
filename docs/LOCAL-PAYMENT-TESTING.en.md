<!-- source: c60796594657 -->
# Local payment testing

Use the official Foundry Anvil local EVM instead of downloading an unfamiliar wallet and trying a real coin payment. Official project: <https://github.com/foundry-rs/foundry>, Anvil: <https://www.getfoundry.sh/anvil/index.html>.

This round pins the official image version v1.8.5 and its image digest. The chain ID is `eip155:31337` and the test native coin is TETH. The node generates a random wallet through the protected local interface, the private key uses the existing encrypted storage, and the public Anvil default private key is not used. Re-initialising does not replace the wallet; when a wallet already exists, overwriting is refused.

## Local environment

- Test chain: `a2n-payment-testchain`, Windows RPC `http://127.0.0.1:18945`, Docker node RPC `http://a2n-payment-testchain:8545`.
- x402 test payee: `a2n-payment-testfacilitator`, Windows `http://127.0.0.1:18946`, Docker `http://a2n-payment-testfacilitator:8546`.
- Two persistent volumes hold the test chain and receipts; the account page shows the local test identity. There is no monetary value and no connection to mainnet.
- The four long-running nodes are now configured with test payment and receipt channels and a limited test budget. The native coin and EIP-3009 test token are still chosen by the settlement coordination layer, and the points module remains independent.

"Create test wallet" on the account page requires the local chain to be running first. The interface is `POST /v1/payment-coordination/test-wallet` with only one field, `rpc_url`; the wallet is created and test balance issued only after the local address, the Anvil client and the fixed chain ID are verified.

`docker/testchain.yaml` defines both the pinned test chain and the long-running facilitator; `docker/Dockerfile.payment-test` and `examples/payments/anvil_facilitator.py` provide the latter's implementation. The test token allows public minting through the local chain's public admin account and must not be used on a real network.

When reinstalling the test environment, first create the existing node network and the base node image `a2n-node-test`, then create the two persistent volumes and start:

```powershell
docker volume create a2n_payment_testchain_v2
docker volume create a2n_payment_testfacilitator_v2
docker compose -f docker/testchain.yaml up -d --build
```

The test chain persistent volume and the facilitator persistent volume must be kept as a matching pair. When only the chain is rebuilt while old receipts and token addresses are reused, the service refuses the wrong state instead of interpreting old records as payments on the new chain. The test RPC and the minting interface bind only to the loopback address.

### Restart and migrating an old environment

On 2026-10-11 it was established that the old Anvil container ran as uid 1000 and had no write permission on root-owned volumes, so the chain snapshot was never flushed. State lost from the old chain cannot be rebuilt from facilitator receipts. The old container was renamed `*-legacy-20261011` and had automatic restart disabled, and its two volumes were kept intact; new v2 volumes were created as a pair and do not reuse the old receipts. The new test token was deployed by a different public test account, so its address differs from the old token. The node's historical accounts and original channel are kept, and historical payments are not re-recognised as paid in this round.

Currently only the test chain container writes to its own test volume as root; Anvil saves a full historical snapshot every five seconds. The facilitator additionally flushes and syncs before deployment, minting, token settlement and simulated dropped responses, saving the chain snapshot before the receipt. Startup waits for RPC readiness and checks every cached receipt for real success on that chain. A restart should be verified with `scripts/payment_testchain_check.py`, which checks real token transfers, receipts, balances and that replaying the same transaction does not pay twice.

Migrating to a new test chain pair also has to handle wallet nonces: the node keeps locally reserved nonces, while account nonces on the new chain start from zero. Run `scripts/repair_local_test_nonces.py` only for this explicit local test migration; after checking the pinned Anvil chain and the test wallet, it aligns the new chain's progress with the retained reserved value; if original transactions are already queued, it advances at most to the earliest original transaction's nonce and mines a block, then continues to check the original transaction. It does not modify the node wallet or accounts, does not fabricate receipts, and does not treat an old transaction as a new success. A normal restart uses the original snapshot and does not need this migration; a real network must not use this script.

After Anvil explicitly changes a nonce it may keep the old queued classification. The repair script verifies the original transaction hash and the wallet signature, writes the original signature bytes into the local encrypted recovery record, then removes the transaction from the test pool and reinserts the same transaction. The recovery record is bound to the current test token and genesis block; state from another test chain cannot be reused. Resending does not produce a new transaction identifier and does not re-run the Agent; the original order's backend later checks the real receipt.

The current automatic settlement acceptance entry point for a real node is `scripts/automatic_settlement_acceptance.py`, and the test chain restart check entry point is `scripts/payment_testchain_check.py`. Workflows, deliverables and independent review are verified by `scripts/vision_completion_acceptance.py`, which produces a new run_id and report by default; resuming an original engineering run requires an explicit `--resume <report path>`, and old results are not treated as a new acceptance of this round. Acceptance records are labelled for controlled use, are not counted in the three-day real calibration, and do not represent independent user approval.

A real wallet can be imported through the existing encrypted keystore configuration entry point. After switching wallets or configuring a real native coin channel, the local test label is no longer shown; mainnet funding acceptance has not been completed.
