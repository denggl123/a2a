<!-- source: 9da3d31d769f -->
# Changelog

## 0.2.0b7 — 2026-10-11

- Desktop, public service, command-line and legacy scheduled-task launches now all use the Windows process protection built into the product; it also caps a single process and its whole process tree at 4 GiB, keeps the replacement process during an upgrade, and imports normally on Linux while preserving the public-node configuration.
- A desktop launch looks for the installed version by the explicit home first; the compatibility wrapper no longer hard-codes the local install path, no longer guesses from an arbitrary listening port or kills an existing process, and the liveness check is read-only.
- The test-chain and facilitator snapshot path and retention count are configurable; snapshots are fully verified as JSON and gzip content, published atomically, uniquely named and rotated within bounds, and a failure returns a non-zero status without automatically restoring accounts.
- Added regressions for the native Windows single-process and whole-tree limits, the survival of the replacement process, Linux public startup, and damaged snapshots and disk failure.

## 0.2.0b6 — 2026-10-11

- The coordination directory keeps the complete signed cards declared by FIND within bounds, and a card is still served by its original digest after a mailbox lease refresh; the directory snapshot and the card cache have a 300-second lifetime, and the card cache holds at most 512 entries and 8 MiB.
- When a remote pagination cursor expires, the source is re-read a limited number of times within the original session and the original quota; candidates already found and quota already spent are kept, and no business call is triggered.
- Added regressions for card refresh consistency, expiry and cache capacity, and recovery from an invalid cursor over real HTTP.

## 0.2.0b5 — 2026-10-11

- Windows encrypted storage reuses DPAPI's native structures and function declarations, fixing a permanent ctypes type retained on every decryption that exhausted memory in long-running processes; existing encrypted data stays readable.
- Added a resource regression for repeated and concurrent decryption on native Windows; network acceptance resumes the original session when any node is short of quota or lacks the target path, and pauses once the path is found.

## 0.2.0b4 — 2026-10-11

- When the first reply is lost, a bilateral signed direct link and the sealed relay keep the original task id, query only the original task, and never call the agent again.
- The identity used to query an original order comes from a separate resolution port injected by the node, staying compatible with old records that already lost the remote id; an ordinary third-party A2A peer never guesses a remote task id.
- Added acceptance of lost-reply recovery on real nodes, checking the result, the signed receipt, a single execution and a single sample, across old records and both paths.

## 0.2.0b3 — 2026-10-11

- Unified the current project status, the six-layer architecture, data ownership and the supported tool entry points; historical designs and reports keep their original dates.
- Removed test scratch files and dead demo scripts for the old platform that had been committed by mistake; the test-directory ignore list now covers several temporary directory names.
- An automatic call keeps a record only after the quote is verified, and a task that has a quote but no order yet still counts in the bounded queue; recovery of an original order keeps priority.
- Recovering an original task clears a stale query error once a valid state arrives, and a terminal state clears the stale unknown-result marker; automatic orders gained a full summary.
- Acceptance now uses the current image and LAN configuration; workflow acceptance creates a new run by default, and explicitly resuming a historical run is marked separately.
- Added read-only inspection of source, release and the four nodes, and test services joined a rebuildable Compose configuration.

## 0.2.0b2 — 2026-10-11

- Ordinary SDK calls now use the consumer's settlement authorisation, and a stable task id fixes the quote and the original order; the per-trade entry point is kept.
- Automatic orders are persisted, with bounded background recovery, backoff and diagnostics; an order already paid is still reconciled after a restart or after the authorisation is switched off.
- Private eight-dimension preference learning suggestions: authorised human reviews, held-out validation over time, small adjustments, and a version/retraction check before adoption; public credit and payment rules are not changed.
- Fixed the permissions of the local Anvil snapshot directory, persisted the paired test volume and trades afterwards, and rejected an old receipt being matched to a new chain.
- Updated stale package descriptions and acceptance boundaries; the source package includes package descriptions, and the five packages and the Windows installer share one version.

## 0.2.0b1 — 2026-10-09

- Apache-2.0 licence, NOTICE, contribution and security notes, a unified current documentation entry point and pre-release rules.
- One version across the five packages and the product, deterministic source packages, an installer that embeds the version and source digest, and checks that the signature agrees with the actual run.
- An automatic acceptance workflow and a browser acceptance entry point for an isolated node; CI candidates and the release publisher signature are kept apart.
- Provider settlement support list, consumer order and authorised quota, a pure matching interface, automatic calls on a fixed plan and recovery from interruption; per-trade confirmation is kept.
- The first ten samples skip points and every payment entirely; switching the automatic authorisation off blocks new payments, while an unknown original order can still be recovered.
- MCP Streamable HTTP tool preview and integration, reusing the single business chain, encrypted accounts, public samples and task idempotency.

These versions are Beta. Long-term calibration with independent real users, real public-network/NAT paths, a mainnet funding security audit and full cross-protocol interoperability each keep their own acceptance boundary.

## 0.1.0

The earlier single-node market, public discovery, free public samples, two-way reviews, points, and native-coin/x402 payment coordination. Stage records stay in docs and the conclusions of that time are not rewritten after the fact.
