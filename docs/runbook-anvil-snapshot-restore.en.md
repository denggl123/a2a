<!-- source: 3c14e3eb9408 -->
# Local payment test snapshot and restore rules

Updated: 2026-10-11, 0.2.0b7. This tool is only for the existing local Anvil test chain and the test facilitator.

## Scope and data ownership

- Chain state and facilitator receipts belong to the persistent test environment; an SDK upgrade does not reset them.
- A snapshot is a read-only backup of existing state; it does not produce payments, issue points or retry transactions automatically.
- The two services use different interfaces: Anvil uses anvil_dumpState; the facilitator uses the in-container public-test-state.json and does not send Anvil RPC to 18946.
- Backups go to snapshots under the current node home by default; --backup-root selects another directory or disk. The default directory is not promised to be on a separate disk or a separate failure domain.
- A snapshot is written to a temporary file, fsynced after full content verification, then atomically replaced; on Linux the directory is synced as well. File names carry a nanosecond identifier so two calls in the same second do not overwrite each other; only complete backups of the same kind are rotated, five kept by default.
- Failure exits with a non-zero code. A read failure, corruption, invalid JSON, gzip CRC error or failed write will not replace an existing backup with bad content.

## Creating a snapshot

Run in a Python environment with the SDK installed:

~~~text
python scripts/anvil_snapshot.py --once --backup-root data/snapshots --keep 5
python scripts/facilitator_snapshot.py --backup-root data/snapshots --keep 5
~~~

The chain RPC defaults to http://127.0.0.1:18945 and can be changed with --rpc; the facilitator container defaults to a2n-payment-testfacilitator and can be changed with --container. Both scripts run exactly one round and create no background process or scheduled task.

Output goes to testchain/testchain-*.json.gz and facilitator/facilitator-*.json respectively. The chain snapshot accepts the raw JSON returned by RPC or gzip hexadecimal content, and rewrites it as canonical gzip after full parsing. A 64 MiB content limit applies both before and after compression.

## Restore rules

A restore changes test chain state and must be performed explicitly by an operator; the script does not choose backups or roll back accounts on its own.

1. Record the chain ID, block height, token address, existing transaction hashes and the SDK's open orders; save the current damaged files.
2. Pause transaction calls to the affected test environment and stop testchain and the facilitator. Do not stop other business nodes.
3. Fully decompress and parse the chain snapshot to be used; fully parse the facilitator JSON to be used. Choose two states that correspond to the same test chain.
4. Put the decompressed chain JSON back into testchain's /state/chain.json and the facilitator JSON back into /state/public-test-state.json.
5. Start the test chain, then the facilitator; check the chain ID, block height, token on-chain code, original transaction receipts and SDK accounts.
6. When receipts or the chain do not match, keep the unknown state, do not resend the original transaction, and do not treat a missing receipt as a successful payment.
7. After the checks pass, create a new snapshot and restore test calls.

Do not overwrite files that are still being written with an old backup; do not treat any two most recent files as a consistent transactional snapshot. The two scripts do not share a transaction lock, so a restore also needs to check the original receipts and chain facts.

## Periodic container flush

testchain.yaml flushes every 60 seconds, and the historical-state snapshot parameter has been removed; these settings take effect after the test chain container is explicitly recreated. External snapshots reduce the impact of damage to a single periodic file, but automatic backups must not be claimed to be running when no schedule has been arranged. This round does not create scheduled tasks for the application, and does not automatically recreate or wipe existing test chain volumes.

## Verification scope

Unit regression verifies complete gzip/JSON, the content limit, that a failed atomic write keeps the old file, unique naming and rotation boundaries. The existing environment additionally runs a read-only snapshot verification; that result is recorded separately from a destructive restore drill. Earlier manual drill information remains in Git history and is not a promise of automatic restore in this round.
