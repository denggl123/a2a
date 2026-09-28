# 架构评估：分层 / 分包 / 解耦

评估日期 2026-09-27。方法：**不看文档怎么说，只看代码怎么连** —— 从 23 个包的
源码里抽出真实 `import a2n_*` 关系，建图、查环、按分层表比对。结论带证据。

## 一句话结论

**依赖结构是健康的**（无环、严格分层、依赖收敛在少数零依赖内核上、资金在包级隔离）；
**问题集中在文档与命名**，不在依赖本身。修一处 README 表层标注 + 给两个包定层定名，
"架构图"和"代码"就能对上。

| 维度 | 评分 | 依据 |
|---|---|---|
| 分层（结构） | **9 / 10** | 按代码自述分层，向上依赖 **0 条** |
| 解耦（包间） | **8 / 10** | 无环；隐式依赖 **0**；hub 收敛在 kernel/store |
| 文档一致性 | **5 / 10** | README 把 `a2n-gateway` 写成 L2，代码自述是 L5 |
| 命名清晰度 | **6 / 10** | `sdk` 名不副实；两个"gateway"、两个"transport" 同义不同 |

## 一、证据：真实依赖图

23 个包，跨包 import 边共 52 条（去重后）。关键指标：

* **强连通分量：无**（Tarjan，无 size>1 的分量）⇒ 图是 **DAG**，没有循环依赖。
* **隐式依赖：0** —— 没有任何"实际 import 了却没写进 pyproject"的包。声明与实现一致。
* **依赖收敛**：`a2n-kernel`（被 18 个包依赖）与 `a2n-store`（17）承担底座；
  `a2n-custodian` / `a2n-settlement` / `a2n-registry`（各 6）是次级枢纽。
* **出度最高**：`a2n-gateway`(10)、`a2n-task`(10)、`a2n-server`(20)。

```
L0  a2n-kernel · a2n-store · a2n-p2p
L1  a2n-ledger · a2n-custodian
L2  a2n-registry · a2n-transport · a2n-reputation · a2n-account
L3  a2n-task · a2n-dispatch · a2n-acceptance · a2n-settlement · a2n-deal · a2n-wallet
L4  a2n-notary · a2n-market · a2n-consensus · a2n-ap2 · a2n-node
L5  a2n-gateway · a2n-server
（a2n-sdk 未标层，见问题②）
```

**分层校验结果：向上依赖 0 条**（依赖只从高层指向低层，无越级反向）。

## 二、问题（按严重度）

### ① README 的分层表把 `a2n-gateway` 写错了一层 —— 唯一的硬伤

* README 第 432 行：`a2n-gateway/  L2  门禁唯一源 gate.resolve`
* 代码 `a2n_gateway/__init__.py` 自述：**「a2n-gateway（L5 入口）：调用编排」**

按 **README 的 L2** 去算，会算出 **4 条向上依赖**（gateway → task / deal / settlement / ap2），
看着像架构违规。按 **代码自述的 L5** 去算，向上依赖 **0 条**。

**裁决：代码是对的，README 表错了。** `a2n-gateway` 不是基础设施（L2），
它是**入口无关的调用编排**（门禁→执行→验收→记账），和 `a2n-server` 同属顶层装配。
这正是"文档漂移"的典型：图错一格，整张图的结论就反了。

**顺带（更正初稿）**：README 那张表**有** `a2n-sdk`，但标的是「独立 零第三方依赖」——
**没给层**；`a2n-node` README 里**是** L4，只是**代码 docstring 没标层**。
两处「文档 vs 代码」的层信息不对称，正好一并补齐（见第四节第 1 条）。

### ② `a2n-sdk` 名不副实，且不在分层表里

* 体量：**5965 行 / 26 文件 —— 全仓最大**，却**零 `a2n_*` 依赖**（完全自足）。
* 内容：`management.py`(635) · `runtime.py`(388) · `upstream.py`(435) · `gateway.py`(535)
  · `storage.py` · `platform_runtime.py` · `client.py` · `web/runtime.html` …
  ⇒ 它装的其实是**本机节点运行时 + 控制台 + 客户端**，不是通常意义的 SDK。
* 谁在用：**只有 `a2n-node`**。分层表把它标成「独立 零第三方依赖」，**没给层**。

**影响**：新人看到"SDK"会以为是个瘦客户端；实际它承载产品运行时。
命名与定位不符，是理解成本的主要来源之一。

### ③ `a2n-node` 一个包里塞了两套装配体（两套持久化栈）

| 装配体 | 驱动 | 用途 | 入口 |
|---|---|---|---|
| `SovereignNode`（`node.py`） | `a2n_store`（领域库） | 自持演示（A─B─C） | `sovereign_demo.py` |
| `Daemon`（`daemon.py`） | `a2n_sdk.storage.LocalStore` | **产品节点** | `serve_public_node.py` / `node_entry.py` |

两个都合理、都是真的，但在同一个包里：读代码的人得先分辨"**哪个才是产品**"。
两者持久化栈不同（领域库 vs SDK 本地加密库），是个实打实的心智负担。

### ④ 命名冲突：两个"gateway"、两个"transport"

* `a2n_sdk.gateway` = 本机回环 HTTP/A2A **适配器**（`_local()` 只服务回环）
* `a2n_gateway` = **L5 调用编排**（门禁→执行→验收→记账）
* 同理 `a2n_sdk.transport`（客户端传输阶梯）vs `a2n_transport`（L2 传输阶梯）

同一名词指两件不同的事，跨包读代码时极易搞混。

### ⑤ 四个"幽灵依赖"（声明了、实际没 import）

`a2n-dispatch`→kernel · `a2n-gateway`→acceptance · `a2n-market`→kernel/ledger
· `a2n-transport`→kernel。无害，但清掉能让 pyproject 更诚实。

## 三、做得好的地方（也有证据）

* **资金隔离在包级落实**：全仓只有 `a2n-custodian` 出现资金系统；`acceptance` /
  `settlement` / `wallet` / `deal` / `account` 全部经它，没有旁路。
* **端点不重复**：`a2n_node.protection` 不是复制 `a2n_sdk.protection`，而是**薄适配器**
  （import 复用 `WindowsProtector`，只加一个 `EnvironmentProtector`）—— 解耦正确。
* **依赖收敛**：底座只有 kernel/store，改了它们影响面大但可预期；
  没有"人人都依赖人人"的泥球。
* **声明与实现一致**：隐式依赖 0，说明 pyproject 不是摆设。

## 四、建议（按性价比排序）

1. ~~**改 README 第 432 行**：`a2n-gateway` 的层改 L2 → **L5**；并给 `a2n-sdk` 补层。~~
   **已落地（2026-09-27）**：`a2n-gateway` 改 **L5**、`a2n-sdk` 标 **L4 本机运行时**、
   `a2n-node` 保留 L4；顺带修了同段的过时测试数（420 → 693）。**架构图与代码自此自洽。**
2. **加一个包级分层守卫测试**（新发现的缺口）：`tests/test_architecture.py` 目前只守
   "钱只在 custodian" 与 "a2n-sdk 内部分层"，**并没有守包级 L0–L5** —— 所以这次
   README 写错层，机器一句话都没说。建议把上面那张 L0–L5 表写进测试，让
   "向上依赖 = 0" 成为**可执行**的规矩，而不是靠人读文档。
3. **给 `a2n-sdk` 正名或改名**：若保留包名，至少在 README/PRODUCT 里写清"它含本机运行时"。
4. **`a2n-node` 拆或分节**：把 `SovereignNode`（演示）与 `Daemon`（产品）在文档里
   明确分成两节，或把演示那套挪去 `scripts/`。
5. **消歧命名**：`a2n_sdk.gateway` → `local_api` / `a2n_gateway` 保持；
   `a2n_sdk.transport` 与 `a2n_transport` 择一改名。
6. **清 4 条幽灵依赖**（可选，低优先）。

> 本评估**只读**地完成了分析；第 1 条（README 表格，纯文档）已按用户 2026-09-27
> 「那改吧」落地。第 2–6 条涉及新增测试或改包名/结构，**未动**，等进一步决定。

