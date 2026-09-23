# a2n-sdk

给 **agent 与程序** 用的门面（管理台是给人用的）。零第三方依赖，只用标准库 `urllib`。

## 一人一个运行时（新入口）

一份 `NodeRuntime` 可以挂多个本地或远程 Agent；localhost A2A 网关让不能直接
集成 SDK 的工作台通过复制 Agent Card 接入。完整设计见
[`docs/SDK-RUNTIME.md`](../../docs/SDK-RUNTIME.md)。

```python
from a2n_sdk import NodeRuntime, RuntimePlatformBridge

runtime = NodeRuntime("did:a2n:me")
runtime.start_gateway(port=8000)
runtime.mount_callable(card, my_agent, service_id="my-agent")

# 兼容当前平台；P2P 以后换成另一个 TransportPort，不改运行时业务。
bridge = RuntimePlatformBridge(runtime)
bridge.publish("my-agent")
```

```bash
pip install -e packages/a2n-sdk --no-deps
```

## 两种角色

| 角色 | 用什么 | 干什么 |
|---|---|---|
| **调用方**（用别人的 agent） | `Client` | 发现、配对、达成交易、调用、对账、出账 |
| **提供方**（把自己的能力挂上网） | `Node` | 发布 Agent Card、取活、执行、上报计量；自带本地管理台 |

---

## 一、调用方：一期（不碰钱）

一期是"关系 + 事实"：没有余额、没有冻结，只有配对、达成、双向计量、对账。

```python
from a2n_sdk import Client

c = Client("http://127.0.0.1:8000", principal="acct:alice")

# ① 维护多个结算账户（对公/对私/海外各一个）
acc = c.create_account("对公-研发线", ref="6222****0001")

# ② 只看支持对等账户的 agent —— 一期唯一能成交的对象
agents = c.discover("ocr-pro", filt={"accepts": ["peer_account"], "region": "cn-east-2"})

# ③ 谈条款、建立配对（没有配对就没有交易）
lk = c.propose_peer(acc["account_id"], agents[0]["agent_id"],
                    terms={"unit_prices": {"call_count": 3}, "net_days": 30,
                           "credit_limit_fen": 100000})

# ④ 达成交易：条款当场快照冻结，事后改条款不影响这一单
deal = c.open_deal(lk["link_id"], "ocr-pro")

# ⑤ 双方各自上报计量（A2N 只记录，不替任何一方下结论）
c.report_deal(deal["deal_id"], "provider",  {"call_count": 120})
c.report_deal(deal["deal_id"], "requester", {"call_count": 118})

# ⑥ 对账：容差内一致 → RECONCILED；分歧 → DISPUTED 且认定金额取较小者
r = c.reconcile_deal(deal["deal_id"])

# ⑦ 出账（幂等：同账期重复调用返回同一张单）
st = c.issue_statement(lk["link_id"], "2026-09")
```

## 二、调用方：调用一个 agent（走治理链）

发现结果里的地址**永远是 A2N 的门牌号**，不是节点真实地址。调用走统一治理链
（门禁 → 建任务 → 执行 → 验收 → 记账），门禁看的是**能力**而不是"有没有配对"：

```python
r = c.call_agent("ag_xxx", skill="ocr-pro", payload={"text": "..."})   # 一次调用，服务端统一判定
print(r["state"], r["result"])                                         # ACCEPTED / SETTLED
```

免费的直接可调；收费的只要你有**任意一种**对方接受的方式即可：对等账户
（**默认**方式）、已绑定的直付渠道（`bind_pay_method` 绑定即自动结算）、或对方
接受 x402（会抛 `PaymentRequiredError`，带凭证重试）。**没配对不会被一刀切挡掉**。

底层原语：`relay()` 走裸中继（内部先 `call_token()` 取凭据），不经建任务/验收，
收费语义只有对等账户——用于"点对点打节点自定义路由"这类底层动作。日常调用用
`call_agent`。

```python
tok = c.call_token("ag_xxx")           # 裸中继凭据（配对或免费才有）
c.relay("ag_xxx", "invoke", {"q": 1})  # 直转节点本地服务
```

拿不到凭据的两种情况：没配对（403）、凭据被篡改或过期（401）。

## 三、命令行：一条命令发现与调用

shell 型 agent / 脚本不必写 Python，直接命令行。输出一律 JSON（机器可读），
出错走 stderr 且退出码非 0（需要付款=3、没资格=4，便于脚本分流）。

```bash
P=http://127.0.0.1:8000

# 发现：按能力找，可按结算方式/属地/单价上限/信誉筛（筛选=偏好，不藏东西）
python -m a2n_sdk discover --platform $P --skill ocr-pro --accept peer_account --limit 5
python -m a2n_sdk discover --platform $P --skill ocr-pro --region cn-east-2 --max-price 300

# 自己的货架（默认精简表；要原始 registry 行加 --json）
python -m a2n_sdk list --platform $P --mine

# 上架：关键字段模式（`--url` 别省，见第四节）
python -m a2n_sdk shelf --platform $P --principal me --skill ocr-pro \
    --url http://10.0.0.9:9102/a2a --price CNY:call_count:3 --accept peer_account

# 调用：走统一治理链，和 Client.call_agent 同一条路
python -m a2n_sdk call --platform $P --principal acct:alice \
    --agent ag_xxx --skill ocr-pro --payload '{"text": "hello"}'
```

复制来的 agent card 也能直接跑：`discover` 拿到的 `agent_id` 就是 `call --agent` 要的值。

`list` 与 `discover` 给**同一张表**（键集一致）：`skills` / `currency`（首选价那一笔的币种）/
`currencies`（全部可收币种）/ `status` / `reachable` / `seats` / `accepts` … 同一份输出
解析器两处通用。列表路不带可达性结论时显式给 `null`（未知），不会编一个 `false` 出来。

## 四、提供方：把自己的能力挂上网

```python
from a2n_sdk import Node

def ocr(payload: dict) -> dict:
    return {"text": "..."}

node = Node(
    card={"name": "my-ocr", "url": "http://localhost/a2a",
          "accepts": ["peer_account"],
          "skills": [{"id": "ocr-pro", "name": "ocr-pro", "tags": ["ocr"]}],
          "x-a2n": {"deployment": {"region": "cn-east-2"},
                    "price_book": {"ocr-pro": {"CNY": {"dimensions": [
                        {"key": "call_count", "amount": 3, "per": 1}]}}}},   # 3 分 = ¥0.03/次
    handlers={"ocr-pro": ocr},
)
node.serve(poll_interval=2.0, local_agent=(8787, ocr), console_port=8770)
```

- **不需要公网 IP**：节点只出站（长轮询取活 / 反向隧道），家宽、NAT、CGNAT 都能跑；
- `local_agent=(port, fn)` 时开启 relay：外部打平台公网入口 → 经隧道 → 你本机的服务，零端口映射；
- 本地管理台 `http://127.0.0.1:8770` 看自己提供了什么、接了多少活。

上架的另一种姿势是命令行（关键字段模式，不用写 Python）：

```bash
python -m a2n_sdk shelf --platform $P --principal me --skill ocr-pro \
    --url http://10.0.0.9:9102/a2a --price CNY:call_count:3 --accept peer_account
```

> **`--url` 别省。** 省略时 SDK 会用占位地址 `http://localhost:9000/a2a` 并在 stderr
> 打一条警告：上架仍然成功、列表里也看得见，但那个地址没有服务在监听 → 平台探测不到 →
> 这一档只能等被拉（pull），**没有常驻进程长轮询就不会被派单**，而你在列表上看不出任何
> 区别。返回里带 `url_is_placeholder`，脚本可据此判断。想让别人真能调到你，就跑
> `Node.serve()` / `scripts/run_a2a_node.py`（自带长轮询），或给一个平台连得上的 `--url`。

## 五、二期（积分/托管）路径已留好

充值 → 发任务 → 提交结果，与一期并行，等托管商接入后启用：

```python
c.deposit("acct:alice", 10000)
t = c.create_task("ocr-pro", {"img": "..."}, budget=100)
c.submit(t["id"], {"text": "..."}, {"call_count": 1, "output_tokens": 42})
c.a2a_state(t["id"])     # 标准 A2A Task 视图
```

## 六、方法速查

| 分组 | 方法 |
|---|---|
| 注册发现 | `register` `heartbeat` `discover` `sync_market` |
| 调用 | `call_agent`（治理链） `relay` `call_token`（裸中继） |
| 一期账户 | `create_account` `accounts` `propose_peer` `accept_peer` `close_peer` `peers` `peer_usable` |
| 一期交易 | `open_deal` `report_deal` `reconcile_deal` `deals` `issue_statement` `statements` |
| 二期任务 | `create_task` `pending_tasks` `submit` `cancel_task` `a2a_state` |
| 资金 | `deposit` `withdraw` `balance` |
| AP2 | `ap2_budget` `ap2_receipt` |
| 计量 | `meter(started, **dims)` |

## 七、已知缺口（还没做）

- Agent Card 尚未做 ed25519 签名发布（A2A v1.0 的签名身份），生产前必须补；
- 节点侧参与一期交易时，计量上报需自行调用 `report_deal`（未与 runner 自动绑定）；
- 无统一的重试/退避策略封装，断网重连由调用方处理。
