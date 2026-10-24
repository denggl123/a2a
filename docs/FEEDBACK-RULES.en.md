<!-- source: e4a02062f423 -->
# A2N two-sided feedback rules (R2)

Version: v0.1, established 2026-09-30. Nature: a rules design used for development and acceptance, and **not yet a statement that the implementation is complete**. It corresponds to batch R2 in [DEVELOPMENT-ROADMAP.md](DEVELOPMENT-ROADMAP.md); the interface contract is in [FEEDBACK-API.md](FEEDBACK-API.en.md) (message version name **a2n-feedback/1**). The business foundation follows [VISION.md](VISION.md): public benefit, zero trade commission, weight reputation and go light on evidence, and the first 10 free calls form samples by default.

## 1. Goal and boundaries

The goal of R2: **after one real task, both buyer and seller can leave signed, unforgeable feedback that matches their role and the state of the task.**

What R2 explicitly does not do (to keep R2 from turning into R3):

| Not done | Belongs to |
|---|---|
| compute a reputation score or blend everything into one personality score | R3 |
| let feedback change ranking, call volume or the chance of being hired | R3 |
| automatic punishment (low score → rate limit / ban) | R3, and "a single bad review never directly causes a network-wide ban" |
| pulling feedback summaries across nodes on demand, network-wide sync | R3 |
| a payment-behaviour dimension | added in R4 once paid calls are enabled |
| adjudicating disputes, automatic refunds | the local "I do not accept this order" (`a2n_sdk/disputes.py`) is a separate line; the two tables are not shared |

## 2. Identity (decided 2026-09-30)

**Buyer and seller both use the node identity: one did = one node.** The feedback author is this node's own `did:a2n:ag_*`, signed with the existing ed25519 identity (`a2n_p2p.identity.Identity`), where the public-key fingerprint is the did. **No account-layer identity is introduced, and no multi-identity or principal header** — consistent with the existing position that "cross-node discovery is an SDK-layer duty and has nothing to do with user identity".

Two boundaries follow from this, and both must be presented honestly:

- Different DIDs are only different identity labels and **do not prove an independent person behind them**; any interface that shows a count of counterparties must carry this note (consistent with the R2 section of the roadmap).
- One computer, one identity: the same person on another node is another did. R2 records the did truthfully and signs it; anti-collusion belongs to R3's influence limits, not to R2's identity verification.

## 3. Two-way feedback and how it maps to state

One real task (`task_id` runs through the tasks/a2a_tasks/deals/pay_charges tables) can produce two independent pieces of feedback:

| Direction | First-version dimensions | Hard gate on state |
|---|---|---|
| Buyer → provider | delivery quality `quality`, punctuality `punctual`, communication `communication` | **`quality` may only be scored for a delivered task (`COMPLETED`/`ACCEPTED`/`SETTLED`)**; a failed or cancelled order can only be reviewed for reachability and experience |
| Provider → buyer | requirements as agreed `on_spec`, communication and cooperation `cooperative` | may be left for every real task state; a normal cancellation or a missing review does not count as bad faith |

Rules:

1. **One current, valid piece of feedback per direction.** A repeat `open` for the same task, direction and author must be rejected (use "edit" instead), and an edit keeps the version without erasing the old one — influence cannot be amplified by submitting over and over.
2. **Scores**: 1–5 integers per dimension; `note` may give one sentence of reason, redacted the same way as samples (email / phone number / key / private key). A dimension may be left empty, and an empty dimension counts as neither a bad nor a good review.
3. **No review without a call relationship**: a review may only be opened against a task that really exists on this node and is linked to that `task_id` — the same discipline as disputes (you can only open a case about a trade that really happened).
4. **Failures recorded as failures**: a network failure is truthfully recorded as a failure, and "a network failure mistaken for poor quality" is an R3 acceptance counter-example; R2's hard gate first ensures `quality` cannot be submitted at all in that case.
5. **A low score and a reasonable refusal do not automatically become malicious behaviour**: R2 has no reputation deduction logic; feedback, disputes and settlement do not affect each other's ledger figures.

## 4. Retaliatory reviews and the "not reviewed" case

- Default shape: **public once both sides have submitted**; delayed publication (publish when the window closes) is a configurable option, and the length of the review window is to be fixed during the small pilot — v0.1 suggests 7 days as a proposal, **not a promise**.
- Not reviewing stays "not reviewed": it counts as neither a good review, nor a block on calling, finishing a trial, or settling. Feedback is never a precondition for a trade.
- Public wording must not promise "permanent": a feedback entry says "retained, editable with versions kept", never "public permanently".

## 5. Signing and what is accepted as evidence

- The feedback object is signed automatically by the author's node identity (`proof = {pub, sig}`, ed25519, unpadded URL-safe Base64, the same convention as the coordination layer); **the other side cannot forge or rewrite a review** — a received item only counts as "sourced" after its signature verifies.
- Feedback that fails verification **is kept verbatim but not accepted as evidence**; the interface says "signature failed" honestly, without silently dropping it or mixing it into valid statistics.
- Self-sourced feedback (a buyer review where `author_did == provider_did`, or an author reviewing its own node) is stored as it is and marked `self_source`; R3 will not count it as independent reputation — R2 sets the marker first.
- An unauthenticated third-party call (feedback with no node signature to show) can only be displayed as an "unverified experience record" and does not enter sourced statistics (that is the R3 consumption rule; R2 first stores the source field correctly).

## 6. Storage and local authority

- It lands in the SDK's local feedback service (`FeedbackBook`), running on the node's `LocalStore` at the same layer as `TrialBook`/`DisputeBook`: **feedback this node wrote itself is the local authority**; feedback received from the other side is "a signed object kept verbatim" — not merged, not signed on their behalf.
- Version chain: an incrementing `revision` plus a `prev_hash` link, so an edit is traceable; it cannot be deleted (a record is not a brochure).
- Counters and versions must be written atomically through `store.tx()` (the R0 lesson: concurrent completion must not lose an update).
- Task-linking fields: `task_id`, `provider_did`, `service_id`, a snapshot of the task state, and the source (`self`/`counterparty`/`third_party_unverified`).

## 7. Where it appears in the console

- The call record row gains "write feedback / edit feedback"; an unreviewed call honestly shows "not reviewed".
- The feedback list panel: author, direction, dimension scores, reason, version number, signature state, source marker; the counterparty count carries the note from section 2.
- A failed fetch does not turn into an empty state; with no feedback it shows an honest empty state and never pretends there are reviews.

## 8. Development batches (design draft, not implemented)

| Batch | Content | Acceptance anchor |
|---|---|---|
| F0 | `FeedbackBook` + storage namespace + idempotency/version chain/state hard gate + pytest | a repeated submission is idempotent; quality cannot be scored before delivery; no task, no review |
| F1 | console: write/edit feedback, the not-reviewed state, the feedback list | page assertions plus screenshots; honest empty state |
| F2 | signed objects and verification of the other side's feedback | forged feedback fails verification, is not accepted, and is still recorded |
| F3 | minimal cross-node exchange (piggybacked on task completion) + full R2 acceptance | one valid review from each side on the same task, measured across nodes |

## 9. Acceptance rules (full R2, each one testable)

1. Both sides leave one valid review on the same task, and neither overwrites the other.
2. Repeated submission is idempotent: replaying the same `Idempotency-Key` returns the original result; a second `open` in the same direction is rejected and points to "edit".
3. An edit keeps versions: the `revision` chain is complete and the old version is readable but not editable.
4. Quality cannot be scored before delivery: the server returns 400 and the message says why.
5. A low score, a reasonable refusal and a normal cancellation trigger no automatic punishment and do not affect settlement.
6. No review without a call relationship: a task that does not exist or does not belong to this node is rejected.
7. Forged feedback fails verification: it is recorded but not accepted, and does not enter valid statistics.
8. Not reviewing blocks nothing: calling, finishing a trial and settling all work end to end.
9. Feedback does not affect the ledger: the four tables hold exactly the same figures as without it.
10. Wording discipline: the interface never says "permanent" or "earn", and an unreviewed item clearly shows "not reviewed".

## 10. References and design ownership

- The R2 section of the roadmap ("R2: trustworthy two-sided feedback first, complex scores later", in DEVELOPMENT-ROADMAP.md) is the source of the requirement; this document makes it executable.
- The old `a2n_reputation.ratings.rate` is reused only for its ideas on task linking, identity checks and de-duplication; its rules "only a buyer may rate a seller, only a successful order may be rated" are **not copied directly** (this design is two-way and mapped to state).
- Scoring algorithms such as `normalize_score` are R3 candidates and do not ship in this batch.
