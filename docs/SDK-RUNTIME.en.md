<!-- source: 2f5fd60e12b7 -->
# A2N node runtime

## In one sentence

**One node represents one person; one runtime can mount many Agents.** Agents may be local, in Docker,
on a LAN or in a remote cloud. Others only see the projection that this node signs and is responsible for delivering; they never touch the real address or account.

Every SDK node provides its own `/console`, A2A access and credential safe: install it on a personal computer and that computer is
the server; install it on a server and that server is the node. The console manages the current node directly, with no need to "connect to yourself".
The platform registry, relay, settlement and other indexes are only optional adapters; a remote console reaches this node through a one-time pairing
only when the user authorises it.

## Layers

**Goal update (2026-09-29)**: discovery is taken over by a separate coordination layer between the network and the business; it is responsible for bounded referral, level-by-level search, pause and resume, and channel plans, while the business layer supplies the satisfaction policy. Every online node on the network carries a basic discovery duty. See [coordination layer rules](COORDINATION-RULES.en.md). The module diagram below records the existing runtime wiring; the new coordination duties are still to be wired in according to those rules.

```text
AI workbench / console
        │ A2A
localhost A2A gateway              a2n_sdk.local_api
        │
call pipeline: transport → acceptance → settlement     a2n_sdk.pipeline
        │              │
TransportPort          AcceptancePort / SettlementPort
        │
platform / direct A2A adapter      a2n_sdk.adapters
self-sovereign node signed direct adapter              a2n_node.sdk_adapter

discovery control plane (carries no task payload)
        │
DiscoveryPort                       a2n_sdk.ports
        │
LAN beacon / bootstrap node / gossip   a2n_node.p2p_service

supply-side network entry
        │
BindingTable → UpstreamPort         a2n_sdk.upstream
                 ├─ Python function
                 ├─ local HTTP/A2A
                 └─ remote HTTP/A2A + local account reference
```

Module discipline:

- The gateway only translates protocols; it does no network selection, acceptance or settlement;
- The call pipeline depends only on four `Protocol`s and imports no platform or P2P implementation;
- The network layer is only responsible for delivery, and must not declare quality passed or touch accounts;
- The supply side only declares "delivered"; acceptance belongs to the caller;
- Account credentials are resolved only in the local safe and do not enter Cards, discovery messages or the platform registry.
- P2P only discovers and verifies Cards; task bodies do not travel over gossip, and discovery does not guarantee address reachability on behalf of the transport layer.

A network path is also just a port implementation. `FallbackTransport` can assemble "self-sovereign signed direct → standard A2A →
platform"; by default it only switches paths when a connection has not yet been established (`UNREACHABLE`), and business rejections and timeouts are not retried
automatically, so that the same task does not produce two side effects. NAT hole punching/QUIC later only needs a new `TransportPort`
adapter, without changing the workbench entry, the call, acceptance or settlement layers.

## Long tasks and cancellation semantics

When a remote A2A `message/send` returns a non-terminal Task, `A2AUpstream` calls `tasks/get` within **the same overall timeout budget**,
until `completed / failed / rejected / canceled`; `input-required` and `auth-required` are returned to the workbench immediately and are not
wrongly polled until timeout. During polling the remote `task id`, `context id`, status description and response metadata are retained, and each query is not
disguised as a new task; exceeding the deadline returns `TIMEOUT` and leaves the last remote Task in the metadata for manual follow-up. The local node persists
the original request encrypted; when the workbench later sends a standard `tasks/get`, it queries once using only the remote task id. If the remote side completes
at that point, the result still returns through the same acceptance and settlement pipeline and does not bypass the business layer. An HTTP
408 or 5xx from A2A/JSON is not arbitrarily written as a business failure; instead it returns `DELIVERY_UNKNOWN` and marks the remote side effect as unknown.

The local gateway provides both `tasks/get` and `tasks/cancel`. "Cancel" here deliberately takes a conservative stance:

- A queued task that has not started can be cancelled before execution;
- A task already running in a Python thread or sent to the remote side cannot be safely killed, so the local node only records the intermediate state
  `CANCEL_REQUESTED`, while the standard A2A state still honestly shows `working`;
- Cancelling a running task explicitly returns `remote_effect_unknown=true`: it means "cancellation has been requested",
  **not that the remote side has necessarily stopped, and not that the side effect has been rolled back**;
- If execution, acceptance or settlement later completes anyway, the final real result overrides `CANCEL_REQUESTED` and retains
  `cancel_requested=true / cancel_acknowledged=false`; the cancellation interface must not be used to hide a real charge;
- After a node crash or restart, a leftover running cancellation request converges to the terminal state `INTERRUPTED`, explicitly keeping the delivery result
  unknown and forbidding automatic replay, instead of being stuck in `CANCEL_REQUESTED` forever;
- For a timed-out or waiting task that already has a remote task id, the local `tasks/cancel` is forwarded as a remote A2A
  `tasks/cancel`; if a fallback transport was used, forwarding is allowed only along the exact path that originally created the task, never switching to another
  provider and wrongly cancelling a task of the same name;
- Only an explicit `canceled` from the remote side sets `cancel_acknowledged=true`. A cancellation request that itself times out, returns 5xx or fails at the protocol
  level is only appended to the original task record, and "cancellation failed" is not disguised as "task failed".

Therefore only a `canceled` returned during the queued stage, or a `canceled` explicitly confirmed by the remote side, means cancelled; an unconfirmed request on a
running task cannot be used as grounds to replay a request with side effects. `TIMEOUT` only ends the local wait, the same task id is not
re-sent as `message/send`, but a read-only `tasks/get` may safely keep tracking the original remote task.

The local execution thread follows the same honest principle: Python cannot safely kill an arbitrary running function. On shutdown the node
first cancels queue items that have not started, waits a limited grace period for running items and persists `INTERRUPTED`; the execution thread is a daemon
thread, so it does not hold process exit hostage forever, and a late result that arrives afterwards cannot overwrite the uncertain state already written to disk at shutdown.

## Local and remote Agents

```python
from a2n_sdk import NodeRuntime

runtime = NodeRuntime("did:a2n:your-node")
runtime.start_gateway(port=8771)

# local function
runtime.mount_callable(
    {"name": "local OCR", "url": "http://127.0.0.1:9102",
     "skills": [{"id": "ocr"}]},
    lambda payload: {"text": local_ocr(payload["image"])},
    service_id="ocr",
)

# remote A2A; the key exists only in the account safe
runtime.add_account(
    "video-cloud", label="my video cloud account",
    headers={"Authorization": "Bearer ..."},
)
runtime.mount_http(
    remote_card, "https://agent.example/a2a", protocol="a2a",
    account_ref="video-cloud", service_id="video", source_kind="remote",
)
```

Both supplies share the same local port:

```text
http://127.0.0.1:8771/a2a/ocr
http://127.0.0.1:8771/a2a/video
```

## Two kinds of projection

### Supply projection

The real Agent Card's address is not exposed. The SDK generates a new card:

```text
real Agent Card
→ source_card_hash
→ url changed to the current node's entry
→ node_did / service_id / projection_chain added
→ the current node re-signs the whole projection card
```

The current node is the party responsible for delivery. If the remote upstream breaks, the projecting node should return a real failure and must not hide the responsibility behind the upstream address.

### Consumer projection

After discovering a network Agent, the local SDK generates one more localhost Card:

```text
network Agent
→ http://127.0.0.1:8771/a2a/proj_xxx
→ copied into any AI workbench that supports A2A
```

The workbench only calls localhost. Identity, network path, account selection, acceptance and settlement are all done by the local SDK.

## Local resource lifecycle

Configuration in the local console is no longer only "add":

- A consumer projection can be removed; after removal the localhost Card stops working immediately and background network observation stops too;
- A supply mount can be paused, resumed or unmounted; when pausing a published supply, first disable it locally at once and withdraw the P2P broadcast,
  then change platform visibility to `private`, stop the tunnel and persist it as `paused`; resuming republishes under the original agent id;
- If the supply is still public on the platform, unmounting is refused by default, and "take it down and unmount together" must be chosen explicitly;
- Taking down on the platform first changes visibility to `private`, and only after success stops the tunnel and deletes the local publication record; if the remote operation
  fails, the local side does not pretend the takedown happened;
- Deleting an account checks references from supply mounts and consumer projections, and refuses deletion while they are still in use;
- An explicit takedown writes a tombstone for the current process, so an asynchronous recovery thread cannot put it back on the shelf from a stale snapshot.

These rules are orchestrated by `RuntimeManagement`; the account safe, the runtime resource table and the platform adapter each handle only
their own state, avoiding stuffing the external publication lifecycle into a low-level container.

## Background network observation

After a network Agent is imported, `NetworkMonitor` starts at once and performs a TCP connect probe at a fixed interval, keeping the
most recent 30 samples (reachability, connection time, timestamp), so the console can draw a latency trend like a VPN client;
it can also be refreshed manually through `/v1/network/probe`.

The P2P control plane sends signed UDP PING/PONG to handshaken neighbours, keeps the most recent 30 RTT samples per neighbour,
and counts received packets, sent packets and drops due to signature or format problems. Probes have a fixed concurrency limit and rotate sampling when there are many neighbours, so a slow node
does not make the background thread pool grow without bound; the console can refresh immediately through `/v1/discovery/probe`.

This metric only says "a TCP connection can be established from here to the other side's HTTP port". It **does not call the Agent,
does not create a task, does not take part in acceptance and produces no cost**, and cannot be passed off as end-to-end duration or a quality score.

The call transport layer has a separate path candidate table. `FallbackTransport` orders the direct / relay / future QUIC adapters by priority
and degrades safely only when "not yet connected" is confirmed (by default `UNREACHABLE`); each call writes the candidates, attempt order and final path into the task's
network metadata. Later queries and cancellations of the task pin the original path and do not re-select. This selection layer is already usable, but it is not NAT hole punching itself.

## P2P discovery integration

A long-running node can attach `a2n-p2p` to the same runtime as a `DiscoveryPort`:

```bash
a2n-node serve --home data/my-node --port 8771 \
  --bootstrap seed.example.com:9701 \
  --p2p-public-base https://my-node.example
```

- On the same LAN, a beacon finds neighbours by default; for a cross-network cold start, `--bootstrap HOST:PORT` may be passed repeatedly. Even with the LAN
  beacon turned off, bootstrap handshakes, keep-alive and expiry cleanup still run independently;
- gossip returns only a compact index within the MTU for the queried skill (HTTP entry, Card hash), and does not broadcast the full directory,
  Cards or task payloads; a handshake must also verify signatures and satisfy `DID = public key fingerprint`. QUERY is accepted only from direct neighbours that have completed a signed
  handshake, preventing unfamiliar UDP packets from using the node for amplification; when results for the same skill exceed one packet, batches rotate;
- Currently a full Card is fetched only from a direct neighbour: the connection is pinned to the source IP that actually sent the signed OFFER, redirects are banned and the
  response body size is limited, then the card signature, owning DID, full card hash, skill and entry are checked; any mismatch means no import.
  Queries and parallel Card fetches share one overall timeout budget, so "seeing a lead through multiple hops" is no promise that the non-adjacent safety boundary can be crossed;
- **The node broadcasts its own supply only when `--p2p-public-base` is explicitly configured.** Without it the node can still discover others,
  but publishes an empty index, avoiding advertising `127.0.0.1` or an unconfirmed internal address as a service others can call;
- `--p2p-public-base` is the operator's explicit statement that "this HTTP entry is reachable by other nodes"; the current
  runtime does not prove public reachability on its behalf. If it is provided by a same-machine reverse proxy, the proxy must preserve `Host`, or pass a matching
  `X-Forwarded-Host / X-Forwarded-Proto`; when the public entry matches exactly, the node allows fetching a projection Card with an identical
  hash and calling its A2A path, but management interfaces such as accounts, configuration and task lists must still
  come from a local console session or an explicit remote pairing. Capability tickets and authentication remain the business entry's responsibility; configuring a URL does not
  automatically open the management port.

The current wiring is "LAN / bootstrap node / signed gossip discovery + standard A2A calls to a declared HTTP entry".
The P2P layer also has UDP hole punching coordinated through active neighbours (cone NAT usually works; symmetric NAT does not, and cross-real-public-NAT
has not been measured) and a voluntary sealed relay. It **still does not include full ICE/QUIC path negotiation**; when both ends are behind
non-mutually-reachable NAT/CGNAT and hole punching fails, discovering the other side does not mean it can be called — a relay should be used instead.
The platform's existing `/v1/relay` is the low-level primitive of the central platform tunnel,
not a decentralised relay that this P2P discovery chain already has.

Likewise, `SettlementPort` is only a decoupled business port. Platform custody settlement and self-sovereign mode's free/bilateral mutual evidence
can be assembled separately, but **general decentralised automatic settlement, multi-currency atomic exchange and cross-node dispute refunds are still not done**.

## Platform compatibility bridge

The existing platform server still establishes one tunnel per card. `RuntimePlatformBridge` encapsulates this historical constraint in an
adapter: for the user there is still only one `NodeRuntime` and one local port, and switching to P2P/QUIC later does not change
mounting, projection or call business.

```python
from a2n_sdk import RuntimePlatformBridge

bridge = RuntimePlatformBridge(runtime, base_url="http://127.0.0.1:18787")
bridge.publish("ocr")
bridge.publish("video")
```

## Projection signing discipline

An original card cannot keep its original signature after its URL is modified. The node SDK's projection is a new card, which must be re-signed by the projecting node
while retaining the original card hash. The platform does not have the supplier's private key and can only generate a compatible view explicitly labelled `attested=false`;
it must not pass it off as a card signed by the supplier.

A formal node assembles the runtime with `a2n_node.runtime_from_sovereign(node)`: the private key and signature implementation stay in
`a2n-node`, and `a2n-sdk` only receives a signing callback. When `NodeRuntime` is constructed directly without injecting a signer,
the projection is explicitly labelled `attested=false` and can only serve as a development or compatibility entry.
