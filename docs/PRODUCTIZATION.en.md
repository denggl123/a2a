<!-- source: 8b7f981d9d4b -->
# Productisation close-out: one-click install plus failure and dispute handling

> This document records the three parts of the "productisation close-out" landed on 2026-09-28, and the parts **deliberately not done**.
> The authoritative basis is [`PRODUCT.md`](PRODUCT.md) (moment 1 / moment 3 / moment 4) and
> [`SOVEREIGN.md`](SOVEREIGN.en.md) (the boundaries in section 4); where they conflict, those documents win.

## 1. One-click install (moment 1: double-click, then do nothing)

```bash
python scripts/bootstrap.py            # dry run to see what it would do: add --dry-run
                                       # only check the environment: add --check
```

On Windows, **double-click** `scripts\bootstrap.cmd`.

- The real implementation is `scripts/bootstrap.py` (standard library only, cross-platform); `.sh` / `.cmd` are just
  thin shells that "find Python and hand over". `.cmd` **must be pure ASCII** (cmd.exe parses by the system code page,
  so Chinese comments get executed as commands — we have been burned by this).
- **Idempotent**: running it again only re-checks, and does not disturb an existing environment; `.venv` is created only when absent.
- **Dry run and real run share the same `plan()`** — this rules out drift between "what the dry run showed" and "what the real run did".
- After installing it **always self-checks** `import a2n_node / a2n_sdk / a2n_p2p / a2n_acceptance / a2n_kernel`
  (`CHECK_IMPORTS` in `bootstrap.py`, i.e. all five packages): the most expensive bug in an installer is
  **false success** (it installs but will not start, the user thinks it is broken and uninstalls). Any failing step exits non-zero at once and says where it stopped.
- Why local packages use `--no-deps`: `requirements.txt` already installs all third-party dependencies, and the `a2n-*` packages are not yet published to PyPI.
- Keep exactly this one path: at install time **there should be no question for the user to answer** (no registration, no preferences, no "do you allow").

## 2. Failure and unknown: present honestly, never disguised as success or income

`docs/PRODUCT.md` moment 3: **money that has not been signed for must not be shown as income; better to show "awaiting the other side's confirmation".**

- The status copy for call records is complete and distinguishes three states:
  - **Completed**: `Settled / Accepted / Done`;
  - **Failed**: `Failed / Rejected / Timed out / Peer unreachable …` (an explicit conclusion);
  - **Outcome unknown**: `Outcome unknown · sent, unconfirmed / Interrupted · outcome unknown / Cancelling · awaiting confirmation`
    — a separate class that is **never lumped in with "completed"**.
- `unknownState(state)` explicitly circles out the three states of "sent but no confirmation received"; the console can use it to
  label them "unconfirmed" instead of counting them as "success". Unregistered states are **echoed as they are**, not guessed.
- Location: `packages/a2n-sdk/src/a2n_sdk/web/runtime.html` (`statusLabel` / `unknownState`),
  with pure-logic assertions in `scripts/runtime_logic_check.js`.

## 3. Disputes: a local "I do not accept this order" (moment 4)

`docs/PRODUCT.md` moment 4: **when the result is bad, the user must have an "I do not accept this order" action, and that action must
itself produce a credential saying "I do not accept it". "There is no arbitrator" does not mean "the user has nowhere to speak".**

Implementation: `a2n_sdk.disputes.DisputeBook` (running on the node's own `LocalStore`) +
the console's "records I do not accept" panel + an inline button on call record rows.

**What it is**: it fixes "who, about which order, why not accepted, and when it was said" into a locally retained record
with a content **fingerprint `digest`**, which can later be used to check whether that record has been altered. It can be **withdrawn** — a withdrawal also
leaves a trace (`state=WITHDRAWN` + `withdrawn_at`) and **does not erase the record** (erasing it = no evidence when things turn nasty later).

**What it is not (the boundary is written into the data)**: `kind="local_rejection_not_arbitration"`.
Self-sovereign mode **has no third-party adjudication** (`SOVEREIGN.md` section 4), therefore:
- **it does not refund automatically** — who holds the money and whether to refund it is the two parties' own business;
- **it does not rule on the counterparty's behalf and does not pretend an adjudication result exists** — the front end must not draw it as "the platform found in my favour";
- **it can only open a case against an order that really exists locally** — rejecting a task that cannot be found is fabricating an accusation with no object.

Historically there was also a **platform-mode arbitration line** (`a2n_acceptance.dispute`, an L4 arbitration ticket running on
`a2n_store`, with adjudication and refund execution); it retired together with the old platform at the 2026-10-05 close-out
(see [RETIRED-PLATFORM.md](RETIRED-PLATFORM.en.md)). **Only self-sovereign mode remains now, and it does not share a table.**

## Deliberately not done (to avoid claiming things that do not exist)

- No arbitration flow, customer-service tickets or operations intervention — that would change the shape of "there is no arbitrator".
- No "rejecting freezes the funds" — self-sovereign mode has no custody, so freezing is an empty phrase.
- No automatic case creation — a proactive interface must answer "what does the user lose by not acting right now" (principle ⑥),
  and if that cannot be answered it must not appear; "I do not accept it" is the user's action, not a system push.

## Verification

| Layer | Command | Result |
|---|---|---|
| Pure logic | `node scripts/runtime_logic_check.js` | all 37 pass (including 7 for failure/unknown and 3 for "I do not accept it") |
| Unit + end to end | `pytest tests/test_disputes.py tests/test_bootstrap.py` | 19 passed |
| Full | `pytest` | 718 passed |
| Browser level | `bash scripts/node_console_check.sh` | all 26 pass (including 2 for the "I do not accept it" empty state) + screenshots + responsive |
