<!-- source: b8b0c79be331 -->
# Release rules and automatic acceptance

## Version, licence and documentation

`VERSION` in the repository root is the single product version, and the five Python packages, the SDK runtime, the source package and the Windows installer must all agree with it. The current value is `0.2.0b7`, a Beta pre-release. Changing the version means updating the five package metadata files, `a2n_sdk/version.py` and the changelog at the same time; `release_check.py` rejects anything missed.

The project code uses [Apache-2.0](../LICENSE), and [NOTICE](../NOTICE) states that the licences of dependencies, user content and external agents are not changed by this project. Current documentation is navigated from [the documentation entry point](README.en.md); past designs are kept but are not a promise of current compatibility or completion. Security issues and the limits on real funds are in [SECURITY](../SECURITY.en.md).

## The automatic acceptance pipeline

`.github/workflows/acceptance.yml` runs on push, on a pull request and on manual trigger:

1. The full test suite on Windows and on Linux/Python, including a real private EVM and a two-node points/MCP trade.
2. Front-end logic checks, and acceptance of the console in a real browser against a temporary isolated node.
3. Metadata, licence, current documentation link and source package integrity checks.
4. A candidate build of the complete Windows installer, running doctor separately and verifying the embedded version, the source digest and the digest of the actual executable.

Remote CI only has a remote result once it has actually been triggered on GitHub; local acceptance reports and remote CI status are kept separate. Tests use random identities, a temporary home and a test chain, and never touch existing business nodes or mainnet funds.

## Building and checking locally

```text
python scripts/release_check.py
python -m pytest tests -q
node scripts/runtime_logic_check.js
python scripts/console_acceptance.py
python scripts/build_sdk_bundle.py
python scripts/build_desktop_bundle.py
python scripts/release_check.py --artifacts --desktop --doctor --require-signed
```

Before developing, install the test-chain dependencies and the pinned builder as the contribution guide says. Browser acceptance needs Node.js, playwright-core and a usable Chromium, and `NODE_EXE`, `NODE_PATH` and `A2N_CHROME` can point at where they live.

The source manifest explicitly excludes local data, databases, wallets, environment files and the publisher private key. Line endings are normalised first, then each file is hashed and the whole source is hashed; gzip/tar timestamps, ownership and ordering are fixed, so the same source produces the same archive. The manifest records the real Git commit and whether the working tree was modified, and never passes a modified build off as that commit.

The Windows package embeds the same version and source digest and contains the five-package runtime, the console, the project licence and the third-party licence list. A source change during the build fails it. doctor really starts an isolated temporary node and checks identity, storage and the core assembly. The release check passes only when the source digest, the file digests, the signature and the runtime result all agree.

## Signing and public distribution

A local build reuses the persistent publisher identity in `data/release-publisher` by default and produces an Ed25519 project signature. The private key and node data are never uploaded. A project signature is not a Windows distribution certificate. Users must confirm the publisher DID through an independent trusted channel; treating the public key inside the same download as the basis for trust is not enough.

CI builds candidates with `--unsigned` and never generates or downloads the real publisher private key. A candidate is only a workflow artifact. A release is published by the maintainer after being rebuilt on a trusted machine, fully accepted and signed with the persistent identity; `require-signed` rejects an unsigned candidate. The workflow does not create a GitHub Release or publish to PyPI automatically.

The public files must include at least: A2N.exe, the matching `.release.json` and desktop manifest, the server source package and its manifest, SHA256SUMS, the current rules and changelog, and the acceptance summary. Never upload data, doctor's temporary identity, wallets or private workspace configuration. Before upgrading an existing node, check the original identity and the backup; after upgrading, read that node's actual runtime version. A passing build does not mean a running old installation has been upgraded.
