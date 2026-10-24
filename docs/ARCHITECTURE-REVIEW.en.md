<!-- source: e9913b2ce31d -->
# Architecture review: layering, packaging, decoupling

Review date 2026-09-27. Method: **ignore what the documentation says and look at how the code is wired** — extract the real `import a2n_*` relations
from the source of 23 packages, build the graph, look for cycles, and compare against the layering table. Conclusions come with evidence.

## Conclusion in one sentence

**The dependency structure is healthy** (acyclic, strictly layered, dependencies converging on a few zero-dependency kernels, funds isolated at package level);
**the problems are concentrated in documentation and naming**, not in the dependencies themselves. Fix one README layer label and give two packages a layer and a name,
and "the architecture diagram" and "the code" line up.

| Dimension | Score | Basis |
|---|---|---|
| Layering (structure) | **9 / 10** | Layered as the code describes itself, with **0** upward dependencies |
| Decoupling (between packages) | **8 / 10** | Acyclic; **0** implicit dependencies; hubs converge on kernel/store |
| Documentation consistency | **5 / 10** | The README puts `a2n-gateway` at L2, while the code describes it as L5 |
| Naming clarity | **6 / 10** | `sdk` does not match its name; two "gateway"s and two "transport"s differ in meaning |

## 1. Evidence: the real dependency graph

23 packages, 52 cross-package import edges after deduplication. Key metrics:

* **Strongly connected components: none** (Tarjan, no component with size>1) ⇒ the graph is a **DAG**, with no circular dependencies.
* **Implicit dependencies: 0** — no package is "actually imported but missing from pyproject". Declaration matches implementation.
* **Dependency convergence**: `a2n-kernel` (depended on by 18 packages) and `a2n-store` (17) carry the foundation;
  `a2n-custodian` / `a2n-settlement` / `a2n-registry` (6 each) are secondary hubs.
* **Highest out-degree**: `a2n-gateway`(10), `a2n-task`(10), `a2n-server`(20).

```
L0  a2n-kernel · a2n-store · a2n-p2p
L1  a2n-ledger · a2n-custodian
L2  a2n-registry · a2n-transport · a2n-reputation · a2n-account
L3  a2n-task · a2n-dispatch · a2n-acceptance · a2n-settlement · a2n-deal · a2n-wallet
L4  a2n-notary · a2n-market · a2n-consensus · a2n-ap2 · a2n-node
L5  a2n-gateway · a2n-server
(a2n-sdk has no layer assigned, see issue 2)
```

**Layering check result: 0 upward dependencies** (dependencies point only from higher layers to lower ones, with no level-skipping reversal).

## 2. Issues (by severity)

### ① The README's layering table puts `a2n-gateway` in the wrong layer — the only hard defect

* README line 432: `a2n-gateway/  L2  the single source of gatekeeping, gate.resolve`
* Code `a2n_gateway/__init__.py` describes itself as: **"a2n-gateway (L5 entry): call orchestration"**

Computed against **the README's L2**, you get **4 upward dependencies** (gateway → task / deal / settlement / ap2),
which looks like an architecture violation. Computed against **the code's self-described L5**, upward dependencies are **0**.

**Ruling: the code is right and the README table is wrong.** `a2n-gateway` is not infrastructure (L2);
it is **entry-agnostic call orchestration** (gate → execute → accept → book), and belongs at the top assembly layer together with `a2n-server`.
This is a textbook case of documentation drift: one wrong cell inverts the conclusion of the whole diagram.

**By the way (correcting the first draft)**: that README table **does** include `a2n-sdk`, but labels it "standalone, zero third-party dependencies" —
**without a layer**; `a2n-node` **is** L4 in the README, but its **code docstring does not state a layer**.
The layer information is asymmetric between documentation and code in exactly these two places, so both were fixed together (see section 4, item 1).

### ② `a2n-sdk` does not match its name, and is missing from the layering table

* Size: **5965 lines / 26 files — the largest in the repository**, yet with **zero `a2n_*` dependencies** (fully self-contained).
* Contents: `management.py`(635) · `runtime.py`(388) · `upstream.py`(435) · `gateway.py`(535)
  · `storage.py` · `platform_runtime.py` · `client.py` · `web/runtime.html` …
  ⇒ what it actually contains is **the local node runtime + console + client**, not an SDK in the usual sense.
* Who uses it: **only `a2n-node`**. The layering table labels it "standalone, zero third-party dependencies", **without a layer**.

**Impact**: a newcomer seeing "SDK" expects a thin client; in reality it carries the product runtime.
The mismatch between name and role is one of the main sources of comprehension cost.

### ③ `a2n-node` stuffs two assemblies (and two persistence stacks) into one package

| Assembly | Driver | Purpose | Entry |
|---|---|---|---|
| `SovereignNode` (`node.py`) | `a2n_store` (the domain database) | Self-sovereign demo (A─B─C) | `sovereign_demo.py` |
| `Daemon` (`daemon.py`) | `a2n_sdk.storage.LocalStore` | **The product node** | `serve_public_node.py` / `node_entry.py` |

Both are reasonable and both are real, but they sit in the same package: a reader has to work out **which one is the product**.
They use different persistence stacks (domain database vs the SDK's local encrypted store), which is a genuine mental burden.

### ④ Naming collisions: two "gateway"s and two "transport"s (**disambiguated 2026-09-28**)

* ~~`a2n_sdk.gateway`~~ → **`a2n_sdk.local_api`** = the local loopback HTTP/A2A **adapter** (`_local()` serves loopback only)
* `a2n_gateway` = **L5 call orchestration** (gate → execute → accept → book) — unchanged
* Likewise ~~`a2n_sdk.transport`~~ → **`a2n_sdk.tunnel`** (the platform-mediated outbound long channel) vs `a2n_transport` (the L2 transport ladder)

Both modules were renamed with `git mv`, the public API (`TunnelClient`/`serve_local_agent`) is unchanged,
and the old names `a2n_sdk.gateway` / `a2n_sdk.transport` can no longer be imported.

### ⑤ Four "ghost dependencies" (declared but never imported) — **cleared 2026-09-28**

~~`a2n-dispatch`→kernel · `a2n-gateway`→acceptance · `a2n-market`→kernel/ledger
· `a2n-transport`→kernel.~~ All four were removed from their pyproject files and are now **permanently guarded**
by `tests/test_package_layers.py::test_no_ghost_dependencies`.

## 3. What is done well (also with evidence)

* **Fund isolation is enforced at package level**: only `a2n-custodian` contains a funds system in the whole repository; `acceptance` /
  `settlement` / `wallet` / `deal` / `account` all go through it, with no bypass.
* **Endpoints are not duplicated**: `a2n_node.protection` is not a copy of `a2n_sdk.protection` but a **thin adapter**
  (importing and reusing `WindowsProtector`, adding only an `EnvironmentProtector`) — correctly decoupled.
* **Dependency convergence**: the foundation is only kernel/store, so changing them has a large but predictable blast radius;
  there is no "everyone depends on everyone" mud ball.
* **Declaration matches implementation**: 0 implicit dependencies means pyproject files are not decoration.

## 4. Recommendations (**all 6 have landed**, 2026-09-27/28)

| # | Recommendation | Status |
|---|---|---|
| 1 | Fix the README layering table (gateway L2→**L5**, sdk labelled **L4 local runtime**) | ✅ commit `e19c4b1`; also fixed the stale test count in the same paragraph (420→693) |
| 2 | Add a **package-level layering guard test** (turn "upward dependencies = 0" into an executable rule) | ✅ new `tests/test_package_layers.py` (6 items: acyclic / layer coverage / zero upward dependencies / 0 implicit dependencies / 0 ghost dependencies / layering table healthy) |
| 3 | Give `a2n-sdk` an **honest name** (state in the docs that it contains the local runtime) | ✅ both the `a2n_sdk/__init__.py` docstring and the README state that "only half of the name is true" |
| 4 | **Split into sections** the two assemblies in `a2n-node` | ✅ a comparison table added to `a2n_node/__init__.py` (SovereignNode = demo / Daemon = product) + README |
| 5 | **Disambiguate names**: `a2n_sdk.gateway`→`local_api`, `a2n_sdk.transport`→`tunnel` | ✅ both modules renamed with `git mv`, 6 imports + 3 docstrings + 3 tests updated; public API (`TunnelClient`/`serve_local_agent`) unchanged, old names no longer importable |
| 6 | Clear the **4 ghost dependencies** | ✅ removed from the pyproject files of dispatch/gateway/market/transport, and **permanently guarded** by the test from item 2 |

> The first five items (1, 3, 4, 5, 6) touch only documentation, metadata and module names, and change no business logic; item 2 is a new test.
> Afterwards `tests/test_package_layers.py` nails "zero upward dependencies / zero implicit dependencies / zero ghost dependencies"
> down as an **executable** rule — the next person to mislabel a layer or sneak in a cross-layer import gets a red build.
