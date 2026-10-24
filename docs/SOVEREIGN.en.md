<!-- source: a714b15104ee -->
# Self-sovereign mode (no server, no custody): how far off it was, and what was filled in

> **Status note, 2026-10-05**: the `a2n_node.SovereignNode` assembly and the
> `scripts/sovereign_demo.py` demo script described here were deleted at the platform unification close-out; self-sovereign capability (a node carrying its own
> identity, direct calls, bilaterally signed receipts) is now carried by the unified `Daemon → NodeRuntime`, see
> [NODE-UNIFICATION.md](NODE-UNIFICATION.en.md) and [RETIRED-PLATFORM.md](RETIRED-PLATFORM.en.md).
> The implementation record and gap analysis of the time are kept below (the "platform mode" landing points in it have retired).

> The goal, in its original words: **with no custodian (points never go online) it is a decentralised network (no server needed);
> any group larger than two people can discover and call each other.**

The conclusion first: **the discovery layer was never on a server in the first place; the break was in the "call" link.**
In platform mode the action "I call you" was carried by three things — the platform relay delivered the request,
the platform registry supplied the other side's address, and the platform gate decided whether to let it through.
Remove all three and what remains is an **assembly in the shape of a node**: `packages/a2n-node`.
That is what this round filled in, with not one line changed on the platform side.

---

## 1. Gap comparison: item by item, "who did platform mode rely on" → "what is missing without a platform"

| # | Capability | Who carried it in platform mode | What is missing once the platform is removed | How it is done now (landing point) | Status |
|---|---|---|---|---|---|
| 1 | Identity | The platform issued a `uid` and registered it in the `agents` table | Nobody allocates an id and nobody vouches | Identity = public key fingerprint `did:a2n:ag_<sha256(pub)[:24]>`; the card carries `pub`+`sig`, and verifying a card needs no authority (`card.build_card` / `card.verify_card`) | ✅ Filled in |
| 2 | Discovery | Query the platform registry (the `registry` table) | No global directory | Neighbour gossip + on-demand multi-hop queries; HELLO verifies signatures and binds DID↔public key, OFFER returns along the measured reverse path carrying "business entry + card hash" within the MTU by skill, without broadcasting the full directory | ✅ Existing + hardened |
| 3 | Finding the address | Only `/v1/relay/{id}` was published; the real address was never given out | No relay to fall back on, so the address must be supplied by the node itself | The card carries `url` (`card.card_endpoint`); `verify_card(require_endpoint=True)` explicitly rejects a card with "no direct address" — with no relay, leaving it empty means it cannot be called, and it should not pretend otherwise | ✅ Filled in |
| 4 | Calling | `hub.forward` on the platform's behalf + the `gate.resolve` gate | No middleman and no central gate | Each node runs its own HTTP entry (`peer.serve`) and requests go directly to the peer's `/a2n/call`; the request **carries its own DID signature** (replacing platform mode's plaintext `X-Principal` claim) | ✅ Filled in |
| 5 | Supplier-side gatekeeping | Platform gate + listing validation | Nobody performs the gatekeeping | Gatekeeping moves down into the node: `SovereignNode._admit`. By default only open capabilities are admitted; restricted capabilities accept only whitelisted DIDs. **Verify identity first, then talk business** | ✅ Filled in |
| 6 | Delivery credentials | The platform notary wrote the `receipts` table (a central fact) | No central notary | **Bilateral signing**: the supplier signs the delivery receipt, and the caller signs an acknowledgement that references that receipt's signature (a one-way dependency). Each side holds evidence bearing the other's signature (`receipt.sign` / `receipt.ack`) | ✅ Filled in |
| 7 | Local trace | The platform's `receipts` table | Who builds your own database | The node calls `init_db()` itself and stamps **locally** with the existing `a2n-notary` (`task.created` / `task.submitted` / `acceptance.passed` / `delivery.acknowledged`) | ✅ Filled in |
| 8 | Bookkeeping and settlement | Points + custody (total ≡ custody) | No issuer | No custody = no points issuance; `settle_mode` currently has only `free` / `bilateral`, and the latter expresses an agreement between two parties and leaves mutual evidence, not automatic clearing in an arbitrary currency | ⚠️ General decentralised settlement not done |
| 9 | Replay protection | Platform sessions and credentials | The session is gone | Requests carry a `nonce` + time window, and the receiving side deduplicates with `ReplayGuard` (`peer.ReplayGuard`) | ✅ Filled in |
| 10 | Tampering and impersonation detection | The platform rewrites with an audit trail | No auditor | Signature domain = the whole card / the whole body: change one byte and verification fails; `did` must be derivable from the carried public key, so nobody can impersonate another (the same discipline at all three layers: `card` / `receipt` / `peer`) | ✅ Filled in |
| 11 | Withdrawal, disputes, arbitration, after-sales | The platform's `acceptance` + the arbitration workbench | No third-party adjudication | **Out of scope this round** (see the boundaries in section 4) | ⛔ Not covered |
| 12 | Global reputation and ranking | `a2n-reputation` + the market board | No aggregation point | **Does not exist**: trust can only be declared locally by a node (whitelist) or decided by the caller's own accumulated call history | ⛔ Not done by design |

Row 8 in one sentence: **the root cause of "no server needed" is not "the server was replaced" but "there is no ledger that needs to agree".**
As long as points are still being issued, there must be a role that "remembers the total"; remove custody and that role disappears by itself.

---

## 2. Three invariants (in custody-free mode they are not "saved on", they simply never existed)

1. **No issuer** — no custody means no points issuance. A node does not produce money, only facts
   (who called whom, what was done, what the result hashes to).
2. **No central ledger** — facts are signed by both parties and each keeps a copy. If either side rewrites history alone,
   the counterparty's copy is the counter-evidence.
3. **No global directory** — discovery relies on neighbours. How much of the network you can see depends on how many people you connect to.
   This is not a defect; it is "nobody can decide for you whom you should see".

---

## 3. How to run it

```bash
# One node (its own keys, its own database, its own entry)
python scripts/sovereign_node.py --name alice --home data/sovereign/alice \
    --skill upper-case --http-port 9661 --p2p-port 9761 --bootstrap 127.0.0.1:9762

# Safe same-machine demo: A ── B ── C, where all three entries resolve to the same measured source, so A can fetch C's card and call C
python scripts/sovereign_demo.py
```

```python
from a2n_node.home import use_home        # (1) fix the database first (zero dependencies, must come first)
home = use_home("data/nodes/alice")
from a2n_node import SovereignNode        # (2) then import (only now does it touch a2n-store)

node = SovereignNode(name="alice", skills=["ocr-pro"], home=home,
                     executor={"ocr-pro": lambda p: {"text": p["text"].upper()}})
node.start()
card, why = node.find("ocr-pro")          # discover → fetch card → verify card → check hash (none of the four steps may be skipped)
out = node.call("ocr-pro", {"text": "hi"}, card=card)
```

> **The ordering is not a style question, it is a correctness question**: `a2n-store` reads `A2N_DB` at import time and caches the connection,
> so the database path is fixed the moment it is imported. "Quietly writing into someone else's database" is the hardest class of accident to trace —
> the data all looks right, it is just in the wrong file. Hence `use_home` has zero dependencies and must come first.

The eight steps of the demo script (`scripts/sovereign_demo.py`):

1. Three processes, three databases: there is no fourth process in the middle;
2. A sees a lead to C through its neighbours — multi-hop discovery, **all self-reported, not one word verified yet**;
3. Fetch the card from the measured source and verify the signature locally — the identity is self-consistent and the card hash matches what was broadcast
   during discovery; to prevent SSRF, a cross-host non-adjacent target is currently not fetched automatically on the strength of a multi-hop OFFER;
4. Direct call — the request hits C's **own** entry;
5. Mutual evidence — A holds a receipt signed by C, and C records A's acknowledgement;
6. Change one byte and it shows — tampering with the output hash, changing the task_id, impersonating the signer or swapping the acknowledgement all fail verification;
7. Let one more person in — a new node with only one seed address finds another person in the network;
8. Structural evidence — `a2n_server / a2n_custodian / a2n_account / a2n_settlement` are **all absent from the process's imports**.

---

## 4. Known boundaries (an honest list)

1. **Across the public internet there is still a need for a "meeting point" and a genuinely reachable data path.** Same machine or same LAN needs only a beacon; across the public internet at least one side must
   have a reachable address, or there must be a rendezvous that only performs meeting (it takes part in no business, keeps no accounts and sees no content —
   it is a "meeting point", not a "server"). When neither side is reachable through NAT, hole punching or a **voluntary relay** is possible:
   the long-running node already implements **UDP hole punching coordinated through active neighbours** (three-party signed messages; usually works with cone NAT,
   **does not work with symmetric NAT/CGNAT**, and has not been measured across real public NAT); a relay sees metadata (who looked for whom and
   when) but not content, and cannot alter credentials. Full ICE/QUIC path negotiation is still not implemented.
2. **There is no global reputation.** Once centralised, no third party can answer "can this person be trusted" for you.
   The current approach is a local node whitelist plus the caller's own history. Introducing cross-node reputation
   would need a witness mechanism (there is a prototype in `a2n-consensus`), and witnessing itself needs incentives — which leads back to money.
   **This is the most expensive blank in custody-free mode, and it is better not to pretend it exists.**
3. **Bilateral mutual evidence covers only two parties.** Each side's copy is counter-evidence to the other's, which is the strongest guarantee achievable with two parties;
   three or more parties (for example "I say I delivered, a third party says it never arrived") need witnesses, and `a2n-consensus`'s
   2/3 witness weight could carry that, but what the "anchor of witnessing" is without custody needs to be defined separately.
4. **General decentralised settlement, withdrawal, disputes, arbitration and refunds are outside this mode.** `bilateral` only records
   the two parties' agreement and the mutually signed facts; it does not perform cross-currency atomic exchange and cannot execute a refund automatically. A purely free exchange of capability does not need
   these; as soon as real payment in the current product is involved, it has to go back to the custody + arbitration chain.
5. **The key is the identity.** Lose the private key and you are a different person; there is no "recover my account" — because nobody has the right to reissue one for you.
6. **Discovery visibility depends on connectivity.** Connect to one person and you see only that person's neighbours; a multi-hop OFFER is only
   a candidate lead. The current safety policy fetches a full Card only from the measured source address of a signed direct neighbour, so a genuinely cross-host multi-hop
   target still needs direct-neighbour meeting, a trusted proxy or a future end-to-end secure transport layer; "seeing it" must not be written as "it can be called".

---

## 5. Relation to platform mode: the same person, not two identities

| | Platform mode | Self-sovereign mode |
|---|---|---|
| Identity | `agents.agent_id` | `did:a2n:ag_<fingerprint>` (derived from the same key) |
| Card hash | `a2n_registry.card_hash` | **The same function** (`card.card_hash` reused directly) |
| Signature verification | `a2n_p2p.envelope` | `verify_pub` (**the same implementation**) |
| Event names | `task.created` / `acceptance.passed` … | The same names + `delivery.acknowledged` |
| Call conclusion | The return body of platform routing | `CallOutcome`, with the same field shape |

So this is not a fork into "another A2N"; it is the same identity and vocabulary in the shape they take **without a centre**,
and reconnecting to the platform later does not require re-introducing anybody. **Single-source discipline** is a hard requirement here:
fingerprints, card hashes, signature verification and signature domains must not be duplicated anywhere — the moment a copy exists,
the same key computes two identities and the same card has two hashes.

---

## 6. Verification

```bash
python -m pytest tests/test_sovereign.py -q      # 34 items (unit + real-process integration)
python scripts/sovereign_demo.py                 # 8-step demo, all green, exit code 0
```

- **Unit** (no database, no network): card self-proof (changed field, changed did, swapped signature, missing piece, no entry),
  bilateral signing (changed field, impersonation, third-party signing, uniqueness of the acknowledgement reference), request signing (payload tampering, claimed identity,
  replay, clock outside the window, response binding), and `use_home`'s database-ownership discipline.
- **Integration** (real processes, real ports, two databases): two nodes discover each other → fetch and verify cards → direct call →
  the receipt verifies in **another process** → the supplier records the acknowledgement; plus "the node blocks what should be blocked before doing any work"
  (401 identity / 403 capability / 403 admission).
