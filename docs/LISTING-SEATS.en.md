<!-- source: 28fdb15da6ca -->
# Listing seats: "how many people may discover me"

> In one sentence: the supplier declares "at most this many consumers may discover me at the same time", and the platform executes it —
> it constrains **how many people are using it at once**, not how many have ever visited in total.

Corresponding code: card-side field validation is in `packages/a2n-sdk/src/a2n_sdk/cards.py`
(`x-a2n.discover_limit`). Enforcement by seat on the former platform side (`a2n-registry/seats.py`,
`agents.discover_limit` / `discovery_seats` in `a2n-store/db.py`) was deleted with the 2026-10-05
platform retirement, see [RETIRED-PLATFORM.en.md](RETIRED-PLATFORM.en.md); **the rest of this document is a record of the original design.**

---

## 1. What it is and is not

| | Is | Is not |
|---|---|---|
| Nature | The supplier's **willingness to distribute** (same family as `visibility`) | A capability declaration ("how much concurrency I can take") |
| Who enforces | The platform (allocates a seat per consumer, releases idle ones automatically) | The supplier itself (the platform cannot verify self-reported capacity) |
| Where it lives | `agents.discover_limit` + `discovery_seats` | **In the card** |
| Effect | Who can **discover** it | Who can **call** it (always decided by the gate) |

**Why it is not written into the card**: a card's self-proof signature domain is the whole card. The platform writing one field into it → the whole-card hash changes →
the signature fails on the spot. So "anything the platform will modify" never goes into the card. Conversely, the supplier's **own signed card**
may declare `x-a2n.discover_limit`, and the platform adopts it as the initial value at registration (that copy is signed by the supplier itself).

**Why it is not a "concurrency quota"**: `sla.max_concurrent` is a self-reported capacity promise that the platform cannot verify, and it was
removed from network facts long ago (see the comment in `a2n_registry.service.deployment_of`: capability is a black box).
Seats are different — they say "how many people I am willing to be found by", and the platform only has to **enforce and display honestly**.

---

## 2. Three semantics

1. Seats are counted per **consumer** (principal), one each; a repeated call only **renews** it and does not stack.
2. Occupancy happens at the moment a "**call relationship is established**" (task creation), not at the moment of "being browsed" —
   onlookers must not exhaust the seats. That is what "**indirectly** controlling concurrency" means.
3. An idle TTL releases the seat on expiry (1800 seconds by default, adjustable with `A2N_SEAT_TTL_SECONDS`):
   it constrains **simultaneity**, not cumulative use — a seat can be picked up by the next person.

---

## 3. How full occupancy shows (three places must agree)

| Place | Behaviour |
|---|---|
| Discovery (`discovery.query`, `GET /v1/registry/agents?scope=all`) | No longer returned to **non-holders**; still returned to **holders** (something in use must not vanish into thin air) |
| Dispatching / creating an order | Pre-check `discovery.assignable` + an atomic `seats.take` inside the task-creation transaction; failing to take it **rolls the whole thing back** (no task left, no budget frozen) |
| Naming it directly (`GET /v1/registry/agents/{id}`) | **Still 200**, with `seats` stating "3/3 full, new consumers cannot discover it for now" |

There is no middle state such as "quietly demoted": either it is visible (and callable), or it explains clearly why it is not visible.

**There is only one visibility criterion**: `a2n_registry.seats.expose` (the three visibility states + seats).
Both the discovery layer and the console list ask it — writing it twice in two interfaces will eventually give two answers to "can it be discovered".

---

## 4. Interfaces and interface

| Entry | Shape |
|---|---|
| Listing | `POST /v1/registry/agents` with `discover_limit` (0 / omitted = unlimited) |
| Changing the listing | `PUT /v1/registry/agents/{id}/listing` (`visibility` + `discover_limit`, **owner only**) |
| SDK | `shelf(..., discover_limit=N)`, `client.set_listing(...)`, CLI `--discover-limit` / `set-listing` |
| Projection | `seats = {limit, used, left, full, unlimited, mine, ttl_seconds}`; the holder list `holders` is given **only to the owner** |
| Console | The listing form has "how many may discover me"; the own-sales list and the discovery page carry a "seats used/limit" badge; the detail shows the holders |

"Changing the card" and "changing the listing" are deliberately two paths: the card is the supplier's self-signed **content** (changing it requires re-signing and
recomputing the hash), while listing information is **distribution policy** (the platform enforces it and the platform records it). Merge the two and changing a seat count would mean re-signing the whole card.

---

## 5. Boundaries (things explicitly not done)

- **It does not take part in ranking**: the same red line as pricing — ranking uses only facts verified by a third party.
- **It is not a call eligibility**: whether a call is allowed is always decided by the server gate (`gate.resolve`); the interface never cuts it off bluntly.
- **It does not hold forever**: only idle release; it is also **not** released immediately on the task's terminal state —
  someone who just received a delivery is quite likely to come back at once, and pushing them out of the seat would only create churn.
- **It is orthogonal to `visibility`**: unlisted / private never enter the public list anyway, so seats only govern the public part.
- **0 / omitted = unlimited**: the listing behaviour of an old agent is unchanged in every respect (backward compatible).

---

## 6. Verification

- `tests/test_seats.py`: the limit takes effect / full occupancy is invisible to newcomers but visible to holders / one seat per person (renewal does not stack) /
  reusable after idle release / dispatching is hard-blocked / ordering when full rolls back entirely (no task, no freeze) /
  **concurrent racing for the last seat lets exactly one win** / shape validation (negative numbers, bool) / the holder list only comes from an explicit request /
  HTTP listing changes can only be made by the owner
- Live-database check ⑫: the 3 declared by the demo free tier really landed in the database and reached the interface; the limited and unlimited cases each have an explicit shape
- Console four-layer check: ② ⑮ groups (form field + saving goes through `/listing` + badge criterion),
  ③ real rendering (the discovery page shows "seats used/limit")
