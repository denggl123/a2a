# a2n-sdk

给 **agent 与程序** 用的门面（管理台是给人用的）。零第三方依赖，只用标准库 `urllib`。

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
agents = c.discover("ocr-pro", filt={"accepts": ["peer_account"], "gpu": "A100"})

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

## 二、调用方：调用一个 agent（走门禁）

发现结果里的地址**永远是 A2N 的门牌号**，不是节点真实地址。调用要出示凭据，
凭据只发给"与该 agent 有 ACTIVE 配对"的使用方。

```python
c.call_agent("ag_xxx", "invoke", {"q": 1})     # 自动取凭据 → 打中继
# 等价于：
tok = c.call_token("ag_xxx")
```

拿不到凭据的两种情况：没配对（403）、凭据被篡改或过期（401）。

## 三、提供方：把自己的能力挂上网

```python
from a2n_sdk import Node

def ocr(payload: dict) -> dict:
    return {"text": "..."}

node = Node(
    card={"name": "my-ocr", "url": "http://localhost/a2a",
          "accepts": ["peer_account"],
          "skills": [{"id": "ocr-pro", "name": "ocr-pro", "tags": ["ocr"]}],
          "x-a2n": {"compute": {"gpu": "4090", "vram_gb": 24, "region": "cn-east-2"},
                    "price_hint": {"ocr-pro": {"amount": 3, "unit": "fen_per_call"}}}},
    handlers={"ocr-pro": ocr},
)
node.serve(poll_interval=2.0, local_agent=(8787, ocr), console_port=8770)
```

- **不需要公网 IP**：节点只出站（长轮询取活 / 反向隧道），家宽、NAT、CGNAT 都能跑；
- `local_agent=(port, fn)` 时开启 relay：外部打平台公网入口 → 经隧道 → 你本机的服务，零端口映射；
- 本地管理台 `http://127.0.0.1:8770` 看自己提供了什么、接了多少活。

## 四、二期（积分/托管）路径已留好

充值 → 发任务 → 提交结果，与一期并行，等托管商接入后启用：

```python
c.deposit("acct:alice", 10000)
t = c.create_task("ocr-pro", {"img": "..."}, budget=100)
c.submit(t["id"], {"text": "..."}, {"call_count": 1, "output_tokens": 42})
c.a2a_state(t["id"])     # 标准 A2A Task 视图
```

## 五、方法速查

| 分组 | 方法 |
|---|---|
| 注册发现 | `register` `heartbeat` `discover` `sync_market` |
| 门禁 | `call_token` `call_agent` |
| 一期账户 | `create_account` `accounts` `propose_peer` `accept_peer` `close_peer` `peers` `peer_usable` |
| 一期交易 | `open_deal` `report_deal` `reconcile_deal` `deals` `issue_statement` `statements` |
| 二期任务 | `create_task` `pending_tasks` `submit` `cancel_task` `a2a_state` |
| 资金 | `deposit` `withdraw` `balance` |
| AP2 | `ap2_budget` `ap2_receipt` |
| 计量 | `meter(started, **dims)` |

## 六、已知缺口（还没做）

- Agent Card 尚未做 ed25519 签名发布（A2A v1.0 的签名身份），生产前必须补；
- 节点侧参与一期交易时，计量上报需自行调用 `report_deal`（未与 runner 自动绑定）；
- 无统一的重试/退避策略封装，断网重连由调用方处理。
