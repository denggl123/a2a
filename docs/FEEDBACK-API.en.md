<!-- source: 96aa1d5f8a60 -->
# A2N bilateral feedback interface contract (R2)

Status: **v0.1; R2 (F0–F3 plus three read-only interfaces) has fully landed**. This document turns [the bilateral feedback rules](FEEDBACK-RULES.en.md) into a development interface; the message version name is **a2n-feedback/1**. The direction and identity basis have been confirmed by the product side (**both buyer and seller use a node did, one did = one node**), while fields, paths and default values still need implementation and pilot validation. Landed so far: the local ledger (`a2n_sdk.feedback.FeedbackBook`), `POST /v1/feedback/open|revise|deliver`, the read-only `GET /v1/feedback` (paged `FeedbackView[]`), `GET /v1/feedback/{feedback_id}/versions`, `GET /v1/feedback/summary` (facts and per-dimension averages only), node identity signing and verification (`a2n_node.feedback_identity`), and the console's "bilateral feedback" panel (with "versions / summary" buttons that call the real read-only interfaces). Cross-internet two-machine acceptance has not been done.

Written: 2026-09-30. Every path in the tables below is already in the code (`tests/test_feedback_read_api.py` verifies the three read-only interfaces on a real gateway).

## 1. Invariants

- Feedback key = **(task_id, direction, author_did)**; one currently valid piece of feedback per direction, and an edit = a new `revision`, without deleting the old version.
- Author identity = node identity `did:a2n:ag_*` (an ed25519 public key fingerprint); `proof = {pub, sig}`, unpadded URL-safe Base64, the same basis as the coordination layer's `Proof`.
- The `quality` dimension is **only** allowed on tasks whose state is `COMPLETED`/`ACCEPTED`/`SETTLED` (`ACCEPTED` = the caller's local projection of a successful "accepted" state); this is a hard server-side gate, and a client-side hint is not a gate.
- Feedback writes no accounts against either settlement or disputes: a feedback operation produces no ledger change.
- Feedback received from the counterparty is **kept as it is after signature verification**; it is not merged into one's own feedback table, not signed on their behalf and not transcribed.

## 2. Public data shapes

| Type | Required fields and types | Semantics |
|---|---|---|
| SignedFeedback | v: "a2n-feedback/1"; feedback_id: string; task_id: string; direction: string; author_did: string; counterparty_did: string; provider_did: string; service_id: string; task_state: string; dimensions: object; note: string (nullable); source: string; at: integer; revision: integer; prev_hash: string ("" for the first version); proof: Proof | One complete piece of feedback; the signature covers the whole original object except `proof` |
| dimensions | `{quality?, punctual?, communication?, on_spec?, cooperative?}`, each an integer from 1–5 and may be omitted | Omitted counts as neither a bad nor a good review; `quality` is subject to the state gate |
| direction | `buyer_to_seller` or `seller_to_buyer` | The author and counterparty are checked by the server against the task's actual roles; the client's own claim is not trusted |
| source | `self` / `counterparty` / `third_party_unverified` | Written locally / carried with the counterparty's signature / a third-party record with no node signature |
| Proof | {pub: string, sig: string} | `did:a2n:ag_*` must equal the fingerprint of `pub`; a mismatch is rejected |
| FeedbackView | SignedFeedback + verified: boolean + self_source: boolean | A display-layer view; `verified=false` is kept as a trace but excluded from the valid statistics |

Time fields are Unix seconds. The implementation rejects empty identifiers, dimension values outside 1–5, unknown directions, an oversized `note` (≤ 500 characters recommended) and unknown mandatory fields; unknown fields that are explicitly defined as optional may be ignored.

## 3. Protected local HTTP interfaces

All `/v1/feedback/` interfaces reuse the existing management-plane checks for local origin, Host/Origin and pairing token (the same layer as `/v1/disputes` and `/v1/projections`).

| Method and path | Request essentials | Response |
|---|---|---|
| POST /v1/feedback/open | task_id, direction, dimensions, note; `Idempotency-Key: command_id` | 201 FeedbackView; a replay with the same key returns the original result; an existing valid feedback in the same direction → 409 pointing to revise; a task that does not exist or does not belong to this node → 404; rating `quality` without delivery → 400 |
| POST /v1/feedback/revise | feedback_id, dimensions, note; `Idempotency-Key` | 200 with the new FeedbackView version (revision+1, prev_hash linked); the old version is readable but not editable |
| POST /v1/feedback/deliver | task_id, scope | **F3 supplementary delivery**: carries this node's buyer feedback to the supplier once more with **the same receipt acknowledgement**, and accepts the feedback the counterparty carries back with the response. Returns `{delivered: bool, received: bool, reason?}`; no acknowledgement, no written feedback or an offline counterparty all give `delivered=false` with a `reason` (no pretending it was delivered) |
| GET /v1/feedback | optional task_id, direction, counterparty, cursor, limit | A paged list (FeedbackView[]), including signature state and source marker |
| GET /v1/feedback/{feedback_id}/versions | — | The version chain (old versions read-only) |
| GET /v1/feedback/summary | counterparty or provider_did | **Facts only**: counts, per-dimension averages (with sample count stated), most recent update time, counts of self_source and unverified. **No reputation score and no grade** — that is R3 |

## 4. Minimum exchange between nodes (R2 only piggybacks)

R2 does not provide an on-demand fetch or summary service (that is R3). The minimum path: **the signed feedback object piggybacks on the task's terminal message**, and the receiver verifies the signature and keeps it as is with `source=counterparty`:

```
task terminal message.metadata.feedback = SignedFeedback
```

- Receiver flow: `verify(proof)` → did == fingerprint → check task_id/direction/role against the local ledger → if it passes, keep it and mark `verified=true`; any failing step → keep a trace with `verified=false` and **do not accept it**.
- Counterparty offline or not piggybacking: this does not affect the validity of the locally written feedback; supplementary delivery uses the same piggyback path (`POST /v1/feedback/deliver` resends the same receipt acknowledgement as the carrier, and the supplier's `acknowledge` is idempotent), with no dedicated retransmission protocol.
- Where direction lands in the implementation: `buyer_to_seller` goes up with the **caller's acknowledgement** (`/a2n/ack`); `seller_to_buyer` goes down with the **supplier's acknowledgement response**. Feedback is usually written after the call, so it is triggered by "supplementary delivery" resending the acknowledgement; feedback on the supplier side is **not pushed proactively** and is delivered when the caller next supplements or re-carries the acknowledgement.
- Like tasks, a feedback object **never resends a version already processed under the same task_id**; replays are handled idempotently and are not stored twice.

## 5. In-process ports (logical signatures)

| Port | Methods | Constraints |
|---|---|---|
| FeedbackBook (a2n_sdk, on top of LocalStore) | open / revise / list / versions / ingest(signed) | All go through `store.tx()` for atomic writes; open checks task ownership and the state gate; ingest only verifies and keeps, and never writes its own rating |
| Node identity adapter (a2n_node) | sign(feedback) / verify(signed) | Reuses `a2n_p2p.identity.Identity` and `verify_pub`; adds no new cryptographic primitive |

## 6. Development batches and acceptance

Batches and acceptance are listed item by item in sections 8 and 9 of [FEEDBACK-RULES.md](FEEDBACK-RULES.en.md) (F0 local ledger → F1 console → F2 verified retention → F3 piggyback exchange + full R2 acceptance). Each batch is accepted on actual behaviour before the next begins; R3 (scoring and ranking) starts only after R2 is fully green.
