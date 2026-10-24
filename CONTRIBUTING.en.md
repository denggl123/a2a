<!-- source: a9077a69b8e5 -->
# Contributing to A2N

A2N is a public-benefit oriented agent supply-and-demand network with zero commission on trades. Read [the current documentation index](docs/README.en.md) first.

The business mainline and the current boundaries are in [project status](docs/PROJECT-STATUS.en.md); the supported installation, inspection and acceptance entry points are in [the script guide](scripts/README.md). Run `python scripts/project_audit.py` to check the source against the release artifacts on this machine; `--installed` explicitly reads the current four nodes and makes no call or settlement.

## Local development

Python 3.11 or newer is required. Run `python scripts/bootstrap.py` to install the five packages, then install `requirements-test-payment.txt` into that environment. Building on Windows additionally needs `PyInstaller==6.22.3`.

Before committing, run:

```text
python scripts/release_check.py
python -m pytest tests
node scripts/runtime_logic_check.js
```

Tests must use temporary directories, test wallets and test coins with no real value. They must not connect to or modify a participant's existing node by default. Real public-network tests are run separately and are not an inference from local tests passing.

## Module boundaries

- The node assembly injects the network, acceptance, payment and execution ports.
- The SDK library depends on the standard library only; a protocol adapter must not read the points ledger, sample counters or reputation policy.
- Discovery only obtains candidates; filtering, personal ranking and settlement run separately.
- The first ten samples skip settlement entirely; an unknown payment or delivery can only restore the original order.
- A new protocol must state its exact version and supported scope, and provide a real HTTP interoperability test plus a failure-recovery test.

## Commit notes

State the purpose, the interfaces changed, how compatibility is handled, the evidence of verification and the remaining limits. When public behaviour changes, update the current documentation, the version and the acceptance in the same change. A past design draft is never the only basis for current behaviour.

Contributions are submitted under the project's Apache-2.0 licence; keep third-party licences and attribution. Never commit wallets, passphrases, access credentials, user ledgers, private tasks or the publisher private key.
