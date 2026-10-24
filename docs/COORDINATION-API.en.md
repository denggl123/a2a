<!-- source: 0cb2f8605d23 -->
# The A2N coordination layer interface contract

Status: **v0.1; the C0 contract and the C1–C3 runtime are in place, and C4 is wired into the local quantity policy and the console (2026-10-05)**. The wire message version is **a2n-coord/1**. The design contract below is kept; the implemented scope, pilot parameters and differences are whatever [SDK-ARCHITECTURE.md](SDK-ARCHITECTURE.en.md) says. A pilot across the public internet is still unverified.

Implementation: `a2n_sdk.coordination` / `coordination_service` / `coordination_api`, and on the node side `coord_identity` / `coord_service` / `coord_network` / `coord_mailbox`. The search interface reuses local authorisation; the six public operations use a signed envelope. Forwarding is currently limited to one explicit neighbour and nested forwarding is not accepted yet. A safe probe only verifies signature metadata, and business reachability stays unknown.

**The error convention is aligned with section 7 (2026-10-05)**: every failure after a successful signature check — rate limiting, insufficient capacity, timeout, not found, request or response too large — returns a **signed ERROR envelope**, with the HTTP status from the section 7 table and `Retry-After` on rate limiting; `body` carries the contract's `message` (senders also read `error` for compatibility). A failed signature check returns only a minimal JSON body (`401 UNVERIFIED_IDENTITY`), is not signed, and leaks no directory. A caller can therefore tell "rate limited" from "wrong identity" instead of seeing a bare status code.

A resume request may carry `preferences` to update the local satisfaction target; it returns BUSY while an in-flight read has not finished. Basic public discovery cannot be switched off, and the optional services are managed separately through `/v1/public-services`. The old `/v1/discovery/search` keeps its results and source labels compatible and also carries a search-session snapshot.

## 1. Invariants and identity

- The offering key is **(provider_did, service_id)**. One offering may have several Cards of different versions or channels; a Card hash is not a permanent offering id.
- Coordination only produces candidates, sources, progress and route plans. **SATISFIED does not mean a purchase**; searching, evaluating, probing and route-plan must not call an agent, consume a trial, accept or charge.
- The referral path and the execution path are recorded separately. B referring C does not authorise B to forward C's business task, and does not prove C is online.
- Every callable channel is bound to the provider's fully verified Card, its **route_card_hash** and its **channel_type**; an arbitrary third-party URL cannot override a signed Card.
- A control-port probe produces only a **control_reachable** observation and cannot establish that a business task is executable. When the task protocol cannot be probed safely, **task_reachable** stays unknown.
- A owns the search queue and the global quota; a remote side handles only bounded requests each time and never recurses on A's behalf.

### 1.1 Public data shapes

| Type | Required fields and types | Meaning |
|---|---|---|
| CandidateKey | provider_did: string; service_id: string | offering identity; neither may be empty |
| NodeRecord | node_did: string; versions: string[]; coord_routes: CoordRoute[]; operations: string[]; issued_at/expires_at: integer; proof: Proof | a node's self-signed coordination entry declaration; not a guarantee of being online |
| CoordRoute | channel_type: direct_https, local_http or coord_mailbox; endpoint: string; optional relay_did: string | used for coordination messages only; a mailbox needs an explicit bearer and lease; local_http applies only to a loopback or LAN range explicitly authorised on that machine, and a remote referral cannot grant that permission |
| Referral | introducer_did: string; target_record: NodeRecord; target_record_hash: string; skill_hint: string[]; observation: string; observed_at/expires_at: integer; proof: Proof | an observation signed by the introducer, keeping the target's self-signed declaration |
| OfferHint | provider_did/service_id: string; skills: string[]; card_ref: object; card_hash: string; observed_at: integer | a hint from the source node; not callable until the card is fetched and verified |
| Candidate | key: CandidateKey; cards: VerifiedCard[]; sources: object[]; routes: RouteDescriptor[]; verification: string | several sources for one offering merged, with Card versions and channels stored separately |
| RouteDescriptor | route_id: string; key: CandidateKey; channel_type: direct_a2a or sealed_relay_a2a; route_card_hash: string; card: object; expires_at: integer; optional relay_did: string | the Card must be signed by provider_did, and the type must match a real transport adapter |
| RouteObservation | route_id: string; measured_by: string; at: integer; probe_kind: string; control_reachable: boolean or null; task_reachable: boolean or null; optional rtt_ms: number | null means unknown; local measurement, self-report and third-party report must be kept apart |

Proof is {pub: string, sig: string}, both using the existing unpadded URL-safe Base64 convention. Time fields are Unix seconds; the expiry boundary is **now >= expires_at means invalid**. The implementation rejects empty identifiers, oversized fields, non-finite numbers and unknown mandatory fields; unknown fields explicitly defined as optional may be ignored, but the signature still covers the complete original object.

## 2. In-process ports

The interfaces are described as logical signatures; a language binding may use dataclasses, Protocol or an equivalent type. The SDK coordination ports keep zero dependency on a2n_node, and node assembles the network and identity adapters.

### 2.1 Local business → CoordinationPort

| Method | Input | Output and constraints |
|---|---|---|
| start(spec, command_id) | SearchSpec | creates search_id, revision=1 and the first round's quota; command_id is idempotent |
| get(search_id) | string | SearchSnapshot; a purely local read |
| pause(search_id, command_id, expected_revision) | identifiers, integers | stops new dispatch and keeps the frontier; idempotent |
| resume(search_id, command_id, expected_revision, round_budget) | the same search_id, a new round's quota | increments round and reuses the conditions and verified candidates; a new logical network request uses a new request_id |
| cancel(search_id, command_id, expected_revision) | identifiers, integers | ends the search; does not cancel a business task already created |
| evaluate(search_id, command_id, expected_revision) | identifiers, integers | hands the current candidate snapshot to the local policy and updates the decision; executes no trade |
| plan(search_id, key, command_id, expected_revision, probe) | CandidateKey, boolean | returns channel preference and observations; probe=true does a safe control probe only and consumes search quota |
| candidates(search_id, result_cursor, limit) | a local result cursor | one page of Candidate summaries, with details readable by key; unrelated to the remote FIND cursor |

SearchSpec contains at least skill: string, required: object, preferences: object, policy_id: string, policy_version: string, round_budget: Budget, auto_continue: boolean; automatic multi-round searching must have a **total_budget: Budget**, and the accumulated spend of all rounds may not exceed it. Exact budgets and private preferences are stored locally only; remote FIND sends only the coarse conditions that are allowed to be public. Budget limits at least the number of remote operations, received bytes, total duration, depth, candidate count, active probes and concurrency. Quota is reserved before each network operation is sent; even when a crash leaves it unknown whether the operation went out, it counts as used.

SearchSnapshot returns at least search_id, revision: integer, progress_revision: integer, result_revision: integer, round: integer, state, spec_version, budget_used, budget_remaining, candidate_count, frontier_count and stop_reason. revision is the control version and increments on a control command, a quota authorisation or a state transition; progress_revision records background progress and quota consumption, and result_revision records changes to candidates and to observations that affect the choice. Ordinary background receipt of messages does not keep changing the control version, so the user is not unable to pause. The enumerated states and default quotas are in sections 7 and 11 of the main rules.

The Budget fields are fixed as remote_operations, received_bytes, duration_ms, introduction_depth, candidate_limit, probe_operations and max_concurrency, all positive integers and never substituted by a boolean. remote_operations covers each actual send, forwarding reservation, card fetch and probe; probe_operations is a sub-quota of it and cannot be treated as extra free network operations. In total_budget, operations, bytes and duration accumulate across rounds; depth and concurrency are ceilings; the candidate count is counted over the set de-duplicated by offering, not summed per round. Time a session spends paused does not count towards duration, but the main rules' session lifetime still applies.

VerifiedCard is {card: object, card_hash: string, verified_at: integer, provider_did: string, service_id: string}. Candidate.verification takes HINT, CARD_VERIFIED, CONFLICT, EXPIRED or INVALID; CARD_VERIFIED only says the declaration passed verification, and callability is still decided separately by channel and business terms. The business `required`/`preferences` in an interface are validated locally by the named policy_id/policy_version, and the coordination protocol never guesses the meaning of unknown business fields.

### 2.2 LocalCandidatePolicy

The local policy interface is **evaluate(candidate_snapshot, policy_version, budget_remaining) → PolicyDecision**. The input is the immutable candidate snapshot at one result_revision, together with sources and unknown fields; the output is decision ∈ {SATISFIED, CONTINUE, NEEDS_INPUT}, reason_codes: string[] and optional selected_keys: CandidateKey[]. A policy may decide to stop, to continue or to ask the human, but cannot create a business task directly. A remote response must never carry policy code to execute. When the policy version changes, it re-evaluates and does not reuse an old conclusion.

Evaluation is triggered when a new candidate or an important observation arrives, at the end of a round, and on an explicit user request, and dense triggers should be coalesced. PolicyDecision returns the result_revision and policy_version it used; the coordination layer checks the version before committing a decision and re-evaluates if it has changed. CONTINUE uses only the currently authorised quota; a new round needs a valid total budget for auto_continue or a new resume command from the user. NEEDS_INPUT pauses automatic exploration and keeps the reason awaiting confirmation.

### 2.3 CoordinationNetworkPort

| Operation | Required result |
|---|---|
| connect(node_record, deadline) | an authenticated session and the actual peer_did; a mismatch fails |
| exchange(session, signed_request, response_cap) | a bounded, verifiable coordination response; reports actual bytes sent and received, and errors |
| safe_probe(coord_route, nonce, deadline) | proves only that the coordination control port responds, recording the observation time and RTT; sends no business task |
| probe_task_route(route_descriptor, nonce, deadline) | checks the target and channel through the safe metadata or control operation of the relevant protocol; with no safe operation it returns unknown and calls no offering |
| forward(session, forwarding_envelope, response_cap) | forwards along the bounded path A specified to a single target; each hop sends only to the named next hop, and the target's original signed response and the forwarding status are returned |

The network port must not automatically treat a control connection made through UDP hole punching as an HTTP/A2A task channel. An adapter must bound parsing, redirects, address classes, response body and timeouts; a public referral must never make a client request loopback or an unauthorised internal management port.

## 3. Local protected HTTP interface

Every **/v1/coord/** local interface reuses the existing management-side local-origin, Host/Origin and pairing-token checks; a reverse proxy being allowed to publish a Card is not management permission. Responses carry search_id, revision and state; the private spec/frontier is never exposed on a public entry point. The paths below are design and do not mean they are implemented.

| Method and path | Request essentials | Response |
|---|---|---|
| POST /v1/coord/searches | SearchSpec; Idempotency-Key: command_id | 201 SearchSnapshot; replaying the same command with the same content returns the original result |
| GET /v1/coord/searches/{search_id} | none | 200 SearchSnapshot |
| POST /v1/coord/searches/{search_id}/pause | If-Match: "revision"; Idempotency-Key | 200 SearchSnapshot |
| POST /v1/coord/searches/{search_id}/resume | as above; body.round_budget | 200 a snapshot of the same search_id in a new round |
| POST /v1/coord/searches/{search_id}/cancel | as above | 200 SearchSnapshot |
| POST /v1/coord/searches/{search_id}/evaluate | as above | 200 SearchSnapshot + PolicyDecision |
| GET /v1/coord/searches/{search_id}/candidates | limit, optional result_cursor | 200 {result_revision, items, next_result_cursor} |
| POST /v1/coord/searches/{search_id}/route-plan | If-Match and Idempotency-Key; key, probe: boolean | 200 RoutePlan; creates no task |

If-Match uses the decimal control revision in double quotes; a mutating operation other than creation that omits it returns 428, and a mismatch with the current version returns **409 REV_CONFLICT** with current_revision and no state change. The idempotent replay is checked before the version: a resend with the same command_id, search, action and content returns the original result even if the current revision has advanced; the same id with different content returns 409 IDEMPOTENCY_CONFLICT. A creation request's command_id is unique within this node's created commands, so a resend cannot create a second search_id. Idempotency records are kept at least until the session expires, and the state change and the recorded command result must commit together. resume is a new round under the same search_id, and a remote next_cursor cannot stand in for the local result_cursor. A result_cursor binds search_id, result_revision, ordering and page size; when the result version changes it returns 409 RESULT_CHANGED, and reading restarts from the first page without losing stored candidates.

State transition constraints: pause moves RUNNING to PAUSED, and an already paused search may be returned unchanged; resume opens a new round only from SATISFIED, PAUSED, BUDGET_REACHED, FRONTIER_EXHAUSTED or an ISOLATED search that has regained an entry point. A RUNNING search may not be resumed again by a new command, and a CANCELLED/EXPIRED one must be replaced by a new search. cancel ends any non-terminal session, and an already cancelled search may be returned unchanged. An illegal transition returns 409 STATE_CONFLICT; an expired session returns 410 SESSION_EXPIRED. An empty result is not a state error.

## 4. Public HTTP and the v1 envelope

| Path (all POST) | Operation | Result boundary |
|---|---|---|
| /public/v1/coord/hello | HELLO | verify the peer's DID, public key, version and CoordRoute; return this node's NodeRecord |
| /public/v1/coord/find | FIND | one page of public OfferHint, Referral and a remote next_cursor; never recurses on the requester's behalf |
| /public/v1/coord/card | GET_CARD | the original signed Card for an exact provider_did/service_id/card_hash |
| /public/v1/coord/routes | RESOLVE_ROUTES | sourced RouteDescriptor hints for a named offering |
| /public/v1/coord/probe | PROBE | a nonce echo and control-port state; executes no agent |
| /public/v1/coord/forward | FORWARD_COORD | hand one coordination request to a single target along the bounded path A explicitly named |

These paths may reuse the existing public **/public/** inbound and reverse proxy, but new routes, authentication, rate limiting and coordination capability declarations must be implemented separately. The /v1/runtime, account, old task relay or business A2A entry points must not be borrowed as coordination interfaces. A node behind a private NAT can answer through an outbound coord_mailbox session with a lease; the task relay supports business tasks only and must not pretend to support FIND.

All six logical operations are foundational interfaces of the new coordination protocol; with no known Card or no reachable forwarding target they return an explicit status, and the interface must not be dropped to pretend full support. Old nodes are marked separately as compatibility sources.

### 4.1 Required fields of operation bodies

The public objects in the table below use the section 1 types. Fields are mandatory by default, and fields marked optional may be omitted; every response is wrapped in the section 4.2 signed envelope.

| Operation | Request body | Success response body |
|---|---|---|
| HELLO | versions: string[]; node_record: NodeRecord; nonce: string | selected_version: string; node_record: NodeRecord; nonce: echoed unchanged; limits: object (accepted size and rate limits) |
| FIND | skill: string; coarse_requirements: object; page_size: integer; cursor: string or null; query_fingerprint: string; max_response_bytes: integer | query_fingerprint; snapshot_id: string; snapshot_revision: integer; offers: OfferHint[]; referrals: Referral[]; next_cursor: string or null; truncated: boolean; limit_reason: string or null |
| GET_CARD | key: CandidateKey; card_hash: string | key; card: object; card_hash; served_at: integer; if the hash is no longer valid it must not silently return another version |
| RESOLVE_ROUTES | key: CandidateKey; limit: integer | key; routes: RouteDescriptor[]; observed_at: integer; truncated: boolean; an empty array when there is no route |
| PROBE | nonce: string | nonce; node_did: string; served_at: integer; the RTT is timed locally by the requester |
| FORWARD_COORD | origin_did/target_did: string; parent_request_id: string; inner_request: object; inner_request_hash: string; path: string[]; deadline: integer; max_response_bytes/max_operations: integer | target_response: object or null; forward_status: string; path-related status and actual consumption; the target response keeps its original signature |

In HELLO the nonce is bound to the handshake together with the envelope's request_id, so replaying an old NodeRecord cannot be taken as proof of being online now. Initially FIND supports querying by capability id, and conditions in required/preferences that have no public mapping yet are handled by the local policy; the whole private object must never be put on the network automatically.

### 4.2 The signed envelope

Every request and every authenticated response uses the following envelope:

| Field | Type and constraint |
|---|---|
| v | integer, fixed at 1 |
| domain | string, fixed at a2n-coord/1 |
| type | HELLO, FIND, GET_CARD, RESOLVE_ROUTES, PROBE, FORWARD_COORD or the corresponding *_RESULT / ERROR |
| request_id | string, unique to this logical request; a resend keeps the original value |
| in_reply_to | string, needed only on a response, equal to the request's request_id |
| sender_did | string, must be derived from proof.pub |
| recipient_did | string, explicitly bound to the intended recipient; a response binds the original requester. A first HELLO with no identity hint may use null and challenge again once an identity is known; other requests must not be empty |
| issued_at / expires_at | integer Unix seconds; a message that has expired or is significantly ahead of local time is rejected |
| body | object, the complete operation-specific body |
| proof | {pub: string, sig: string} |

**Signing domain**: copy the whole envelope and remove only the top-level proof; sign that dict with the existing Identity.sign, verify with a2n_p2p.envelope.verify_pub, and check sender_did with the existing did_from_pub/fingerprint_of. domain, type, request_id, in_reply_to, the validity window and every field of body are therefore covered. The JSON serialisation of Identity.sign is reused: sorted keys, compact separators, ensure_ascii=False, UTF-8; the implementation must reject duplicate JSON keys, NaN/Infinity and numbers that cannot round-trip losslessly when parsing. **This is the project's existing convention and makes no claim to conform to RFC 8785.** An authenticated transport may be used to bind an identity, but a response that can be cached or forwarded still needs the independent signature above.

NodeRecord and Referral each use the same Identity.sign/verify_pub primitives too: build {domain: "a2n-coord-node/1", value: the object without proof} and {domain: "a2n-coord-referral/1", value: the object without proof} respectively, then sign. The introducer's DID must correspond to Referral.proof.pub; target_record is self-signed by the target's DID. target_record_hash is the SHA-256 lowercase hex of the complete signed NodeRecord in the JSON bytes described above; the Referral signature covers that hash, the complete target_record, the capability hint, the observation and the expiry. A second forward must not re-sign and thereby erase the original introducer. OfferHint is covered by the FIND_RESULT responder's signature but remains only a hint; a Card's self-proof still goes through the project's existing verify_card and card_hash, and the Card signing rules are unchanged.

### 4.3 Complete FIND request and response shapes

The examples below are structurally complete, and **the signature and hash placeholders cannot be used for verification**. A FIND query_fingerprint is the SHA-256 lowercase 64-hex value of the public {skill, coarse_requirements, page_size} in the project JSON convention above; it excludes request_id and cursor. Private preferences and the total budget never go on the network.

~~~json
{
  "v": 1,
  "domain": "a2n-coord/1",
  "type": "FIND",
  "request_id": "req_7e92",
  "sender_did": "did:a2n:ag_aaaaaaaaaaaaaaaaaaaaaaaa",
  "recipient_did": "did:a2n:ag_bbbbbbbbbbbbbbbbbbbbbbbb",
  "issued_at": 1790700000,
  "expires_at": 1790700005,
  "body": {
    "skill": "video-edit",
    "coarse_requirements": {"language": "zh"},
    "page_size": 10,
    "cursor": null,
    "query_fingerprint": "0000000000000000000000000000000000000000000000000000000000000000",
    "max_response_bytes": 65536
  },
  "proof": {"pub": "example-public-key", "sig": "example-signature"}
}
~~~

~~~json
{
  "v": 1,
  "domain": "a2n-coord/1",
  "type": "FIND_RESULT",
  "request_id": "rsp_2b41",
  "in_reply_to": "req_7e92",
  "sender_did": "did:a2n:ag_bbbbbbbbbbbbbbbbbbbbbbbb",
  "recipient_did": "did:a2n:ag_aaaaaaaaaaaaaaaaaaaaaaaa",
  "issued_at": 1790700001,
  "expires_at": 1790700031,
  "body": {
    "query_fingerprint": "0000000000000000000000000000000000000000000000000000000000000000",
    "snapshot_id": "snap_b_31",
    "snapshot_revision": 31,
    "offers": [{
      "provider_did": "did:a2n:ag_eeeeeeeeeeeeeeeeeeeeeeee",
      "service_id": "svc_video_1",
      "skills": ["video-edit"],
      "card_ref": {"provider_did": "did:a2n:ag_eeeeeeeeeeeeeeeeeeeeeeee", "service_id": "svc_video_1"},
      "card_hash": "example-card-hash",
      "observed_at": 1790699990
    }],
    "referrals": [{
      "introducer_did": "did:a2n:ag_bbbbbbbbbbbbbbbbbbbbbbbb",
      "target_record": {
        "node_did": "did:a2n:ag_cccccccccccccccccccccccc",
        "versions": ["a2n-coord/1"],
        "coord_routes": [{"channel_type": "direct_https", "endpoint": "https://c.example/public/v1/coord"}],
        "operations": ["HELLO", "FIND", "GET_CARD", "RESOLVE_ROUTES", "PROBE", "FORWARD_COORD"],
        "issued_at": 1790699900,
        "expires_at": 1790700200,
        "proof": {"pub": "c-example-public-key", "sig": "c-example-signature"}
      },
      "target_record_hash": "example-node-record-hash",
      "skill_hint": ["video-edit"],
      "observation": "recently_connected",
      "observed_at": 1790699998,
      "expires_at": 1790700058,
      "proof": {"pub": "b-example-public-key", "sig": "b-example-signature"}
    }],
    "next_cursor": "opaque_b_31_10",
    "truncated": false,
    "limit_reason": null
  },
  "proof": {"pub": "b-example-public-key", "sig": "b-response-signature"}
}
~~~

The receiver checks, in order: size and JSON validity, the envelope signature and validity window, in_reply_to and the public condition fingerprint, the independent signature of each NodeRecord/Referral, and the safe access scope of a Card reference. Until the card has been fetched and passed the provider's signature plus the DID/service_id/hash checks, an OfferHint only enters the pending set. A referral in FIND_RESULT only extends A's frontier and never triggers B or C to recurse on their own.

## 5. Pagination, quota, idempotency and forwarding

A remote FIND cursor is an opaque string issued by B, bound to B's DID, the requester's DID (for a private session), the public condition fingerprint, page_size, snapshot_id/revision and a validity window. B returns the next page of the same snapshot; an expired one returns CURSOR_EXPIRED. A may re-read from that source's first page and de-duplicate by CandidateKey, Card hash, source and cursor access record. A commits the verified pages, the de-duplicated set, next_cursor and the quota already used atomically; a restart must not reset the budget.

A remote side keeps short-term idempotency over (sender_did, type, request_id): the same id with the same signed content gets the original response or an already-processed status, while the same id with different content is IDEMPOTENCY_CONFLICT. **A new logical request uses a new request_id; only a transport-level resend keeps the original id.** Even if a response is lost, every local actual send attempt, every received byte and every verification failure still counts against the global and local limits; a resend cannot expand the budget for free.

FORWARD_COORD's inner_request is the complete envelope A signed for the final target_did, and inner_request_hash is the SHA-256 of that complete signed object in this project's JSON bytes; the inner operation must be one of the other five. A's original forwarding declaration binds origin_did, target_did, parent_request_id, path, deadline and max_response_bytes/max_operations, and forwarding must preserve that original signed declaration. path starts at A's next hop and ends at the target, each DID may appear once, and the maximum number of intermediate nodes follows the main rules.

One intermediate node, A→B→C, is preferred; when an explicitly declared path is longer, each intermediate node forwards the original declaration and the inner request only to the next hop A named, attaching its own authenticated per-hop envelope. A per-hop envelope only adds the current position, the parent request it relates to and consumption information; it cannot rewrite the original path or obtain new quota. The next hop checks the original declaration's signature, the current position, the previous authenticated neighbour, the target's authorisation and the validity window; it cannot add a node, broadcast, nest a new FORWARD_COORD or create a downstream FIND for A. The final target executes the inner request exactly once, the original response keeps the target's signature, and it travels back along the request path.

Before dispatching, A reserves operation, byte and concurrency quota along the path, and B, C and the rest each enforce their own local rate limiting; even if one hop misreports its remaining quota, it cannot bypass the receiving node's local ceiling. When the target is unreachable it returns UNREACHABLE, and when the path type is unsupported it returns UNSUPPORTED_ROUTE — never a fabricated empty result. Forwarding idempotency is bound to the original request, the final target and the current position.

## 6. Route plans and handing over to a business task

route-plan returns {key, plan_revision, choices, preferred_route_id, reason_codes, remaining_budget}. Each choice has at least channel_type, route_card_hash, the complete verified Card, the validity window, the authorisation state and separate control_reachable / task_reachable. A direct link and a sealed relay may have Cards with different hashes, but they must resolve to the same CandidateKey; sealed_relay_a2a may only be handed to an adapter that supports that protocol and must not be silently degraded to an ordinary A2A URL. Unknown task reachability must be shown as unknown.

After the business layer picks an offering, it reconfirms the quote, trial state, version, accepted-order terms and channel validity against the chosen Card and the route-plan, then **independently** creates the business task_id / idempotency key and calls the existing CallPipeline. The task binding stores at least provider_did, service_id, selected_card_hash, channel_type, route_id, transport_route_name and task_id; once the remote side accepts, remote_task_id and context_id are added. The coordination layer does not own the trade task. A route-plan updated during execution applies only to new tasks; querying and cancelling an old task stay on its original channel. Only when a request is confirmed not to have arrived may another equally authorised channel be tried; TIMEOUT or DELIVERY_UNKNOWN keeps the unknown state and queries the task along the original channel first, without re-sending across candidates. A signature, permission or protocol failure must not degrade to a weaker channel.

## 7. Errors and the HTTP mapping

A public protocol error returns a signed ERROR envelope when the peer can be authenticated, with a body of at least {code, message, retry_after_seconds?, current_revision?}; invalid input before authentication returns only a minimal JSON error and leaks no node directory. The HTTP code does not replace body.code. A single-source failure does not erase other verified candidates.

| HTTP | code | Situation |
|---|---|---|
| 200 | OK / NO_MATCH_IN_SNAPSHOT | a valid empty page is also a success and must not be read as the whole network having no supply |
| 400 | INVALID_REQUEST | invalid structure, condition, signing-domain field or cursor shape |
| 401 | UNVERIFIED_IDENTITY | the public key does not match the DID, or the signature fails |
| 403 | FORBIDDEN_TARGET | a forwarding target, address range or operation is not authorised |
| 404 | NOT_FOUND | the exact Card or the local search_id does not exist |
| 409 | REV_CONFLICT / RESULT_CHANGED / IDEMPOTENCY_CONFLICT / STATE_CONFLICT | a local concurrency version, request reuse or state transition conflict |
| 410 | CURSOR_EXPIRED / SESSION_EXPIRED | the remote snapshot/cursor or the local session has expired |
| 413 | REQUEST_TOO_LARGE / RESPONSE_TOO_LARGE | the request or response exceeds the declared and local limits |
| 422 | UNSUPPORTED_ROUTE | the declared path or channel type is not supported |
| 426 | UNSUPPORTED_VERSION | no shared coordination version; old source capabilities are kept |
| 428 | REV_REQUIRED | a local mutation is missing If-Match |
| 429 | RATE_LIMITED | rate limited by identity/connection, with Retry-After |
| 502 | UNREACHABLE | forwarding to an explicit target failed; an empty page must not be fabricated |
| 503 | BUSY | the node's current capacity is insufficient, with a suggested backoff |
| 504 | DEADLINE_EXCEEDED | the deadline passed, keeping the partial results already obtained |

## 8. Compatibility and minimum acceptance

In this implementation a remote directory snapshot is valid for 300 seconds. Signed cards declared by FIND are kept within bounds by digest, and GET_CARD can return the same complete original card while it is valid; refreshing a dynamic mailbox lease does not rewrite a digest already handed out. The card cache holds at most 512 entries and 8 MiB, and an entry evicted for age or capacity returns an explicit NOT_FOUND. CURSOR_EXPIRED automatically re-reads from the first page at most twice per round per source, keeping candidates and the quota already used; it stays subject to the round and total budgets.

The existing a2n_sdk.ports.DiscoveryPort advertise/discover/snapshot can be wrapped as a **compatibility leaf source**: it returns verified Cards but no Referral, no remote pagination and no public coordination capability; so does the old /public/v1/agents. The old P2P seeds and a hand-written public directory can serve as the initial frontier. A node whose HELLO does not support a2n-coord/1 is only marked as missing a capability and loses no business reputation; the original direct/relay calls still follow the original protocol. The new basic public discovery must be separated from optional services such as witnessing and the task relay, and the old public-service switches must not simply be set to true together.

The first contract acceptance covers at least: A→B→C paginated discovery and referral signature verification, A→B→A loop prevention, a repeated request_id conflicting with changed content, restarting across pages and cursor invalidation not resetting quota, two buyers with different local policies, a NAT'd C answering in real time through a dedicated coord_mailbox or explicitly reporting a cached state, a malicious address not reaching an internal network, a control-port probe executing no agent, direct and relay Card binding, and no duplicate execution through a new channel after TIMEOUT. Acceptance output must distinguish in-process, local container and different public-network environments, and must not claim the shortest path or complete discovery across the whole network.
