<!-- source: 486773f4964d -->
# a2n-sdk

This Python package provides the node business contracts, the runtime, the management client and the pure business modules, and depends only on the standard library. The "full SDK" a user installs is the A2N product published from the repository root, which also contains a2n-node, the kernel, networking, acceptance, the payment adapters and the console.

## Installation and current entry points

For installing the full product see [the project README](../../README.en.md); for documentation, [the current entry points](../../docs/README.en.md) are authoritative. A single node listens on `http://127.0.0.1:8771` by default, with the console at `/console`. The old central platform and its account, withdrawal and AP2 interfaces are gone; the current program always connects to its own node.

## Calling from code

```python
from a2n_sdk import NodeClient

# owner_token is the local management credential; never put it in an Agent
# description, input or public sample.
client = NodeClient("http://127.0.0.1:8771", token=owner_token)
task = client.call("my-imported-agent", "inspect", {"text": "text to process"},
                   task_id="my-stable-request-id")
# Query the original order for an undecided outcome; a retry with the same
# task_id must not change the input.
latest = client.get_task("my-imported-agent", "my-stable-request-id")
```

`call` returns an A2A Task. `call_agent` returns the delivered content and accepts the same `task_id / metadata / automatic` arguments. While the task is unfinished it raises NodeRequestError carrying the original task; only when the node proves execution never reached the remote side is it a SafeRetryError. An underlying UNKNOWN must not trigger an automatic re-call of the Agent.

The supplier configures which payment methods it accepts; the consumer configures their order, the amount ceiling and automatic authorisation. Automatic settlement is off by default; once it is on, an ordinary SDK call fixes the quote and the original plan, and the background restores the same order. Turning authorisation off blocks new payments while original orders that already had an external effect keep being reconciled. The first ten real free technical deliveries always become redacted public samples and create no settlement record. For the detailed rules see [automatic settlement](../../docs/AUTOMATIC-SETTLEMENT.en.md).

## Connecting Agents

A full node speaks JSON HTTP, A2A and MCP Streamable HTTP, all through the same call, sample, acceptance and feedback chain. `NodeClient.mount` mounts an HTTP Agent; MCP tools are previewed first, then selected and authorised. For worked examples and boundaries see [the integration guide](../../docs/INTEGRATIONS.en.md).

`NodeRuntime.mount_callable` inside the SDK mounts a function in this process, but a full marketplace service has to be assembled with `a2n_node.Daemon`. The Agent Card is signed and published by the local node; an unverified external Card must not be treated as a signed node.

## Module boundaries

- `ports` defines the call, acceptance, settlement and transport interfaces; `runtime` does the assembly.
- `settlement_policy` holds rules and pure matching only; `payment_coordination` keeps contracts and the unified order. Wallets, on-chain RPC and the concrete payment drivers live in a2n-node.
- `selection` reads normalised features and private preferences; discovery only finds nodes, and filtering and ranking execute no payment.
- `task_quality`, calibration, disputes, feedback and financial records persist independently; a technical delivery is not a semantic quality pass.

This is Beta; the version comes from the root VERSION file. Licensed under [Apache-2.0](LICENSE); for release acceptance see [the release guide](../../docs/RELEASING.en.md).
