<!-- source: 9ff88afe2843 -->
# Two machines on different networks: the next threshold and how to check it

This document covers one thing only: the threshold GPT raised in its 2026-09-27 review — **how far we have got**,
**how to reproduce it with one command**, and **what is still missing for it to count as passed**. It is written as concretely as possible, to avoid
reporting "we saw it" as "we did it".

## The threshold, in its original words

> Two ordinary computers not on the same network, each with a node installed; one runs a real Agent, the other
> discovers it on its own and completes a free call, both sides can verify the same receipt, and after a disconnect and restart it can still continue.

This is one level above "open the console and find a card": what is required is **one piece of evidence that cannot be faked**.

## Where we are now (measured 2026-09-27)

| Step | State | Evidence |
|---|---|---|
| Nodes on two different networks both running | ✅ | Local Windows node (home network, 8890) + public data-centre node (`172.245.148.171`, systemd) |
| Cross-network **discovery** | ✅ | The local node searched the public directory over HTTPS and got the remote card back (`did:a2n:ag_4c356c85…`), `errors=[]` |
| Cross-network **free call** | ❌ | The remote call ended `state=failed`: `URLError: Connection refused` / `a2nState=UNREACHABLE` |
| Both sides verify **the same receipt** | Awaiting a successful call | No delivery → receipt is `null` (**not forged**, correct behaviour) |
| Continue after disconnect and restart | Not tested | Depends on the step above |

**Why the call failed**: the upstream of that remote supply was **deliberately left dead** (`A2N_DEAD_AGENT=
http://127.0.0.1:9/invoke`). The node **really did** try to connect, and the real connection error is reported **as is**.
This is not a script fault; it is the sentence the product wants to say: **a card on the shelf ≠ a live service**. To cross this threshold,
the remote side must run an Agent that **actually works**.

## How to reproduce with one command

**Caller side** (on any machine with the node installed):

```bash
python scripts/cross_network_check.py \
    --node http://127.0.0.1:8890 \
    --source https://<remote node's public entry> \
    --skill <capability id> \
    --call
```

It prints PASS/FAIL for each of "connect directory source / cross-network discovery / select remote card / add to pending use / remote call / read back receipt"
and prints the **real error as is**. The script fetches the local cookie from `/console` itself, so
no pairing code is needed.

**Node self-check** (when it will not start after installation, or cannot discover anyone):

```bash
python scripts/node_doctor.py --home data/local-node --source https://<remote public entry>
```

## What is still missing for it to pass

1. **A genuinely working Agent on the remote side** (not a dead upstream). It can be any real
   HTTP/A2A service; just use its address as the upstream of that supply.
2. **The caller** makes a free call with the command above and gets the delivery result and the receipt hash.
3. **Both sides verify the receipt**: the caller reads its local record (`/v1/calls/detail`) and the supplier reads its own;
   both should be **the same one** (the same `task_id` and receipt hash).
4. **Disconnect and restart**: kill either node's process and start it again, repeat steps 2–3, and it should still hold.

## Honest boundaries (these must be stated, not blurred)

* **The native P2P leg does not work right now, and it is not a code problem.** The local VPN is in **proxy mode (no TUN)**,
  so native UDP goes out but the reply is dropped: the peer sees `{verified_envelope_key:true, reachable:false}`
  with all `dropped_*` at 0. Making it work requires switching the VPN to **TUN / global mode**, which is a network-shape issue.
* So the evidence chain above runs over the **HTTPS public entry / directory source** path, **not native P2P**.
  Do not describe "an HTTPS call through a tunnel" as "P2P is working".
* The **proxy port in this environment is session-scoped** (it changes and expires). If the node inherits a dead proxy for outbound traffic,
  the cross-network hop becomes **intermittently reachable**; `cross_network_check.py` retries 3 times by default and records the jitter honestly.
