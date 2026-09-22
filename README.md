# A2N —— 只发行情、只刻章、不碰钱的 Agent 服务网络

**为什么做这件事 → [`docs/VISION.md`](docs/VISION.md)（最高愿景；与其它文档冲突时，以它为准）**

本地可运行的最小闭环（S0-S2 垂直切片）。

## 启动

```bash
# 1. 装第三方依赖
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt

# 2. 装 23 个本地包（**别跳**：根 pyproject 只放 pytest 配置，
#    requirements.txt 里不含 a2n-*；少了这步下一步会 No module named 'a2n_server'）
bash scripts/install_all.sh

# 3. 起服务
.venv/Scripts/python -m uvicorn a2n_server.app:app --port 8000

# 4. 灌演示数据（另一个终端）
.venv/Scripts/python scripts/seed.py

# 5. 打开管理台
#    http://127.0.0.1:8000/console
```

### 演示环境：一条命令起 4 个节点（本机 1 + 容器 3）

**docker 在这里扮演的是"公网上的另外几台机器"** —— 节点真正上线时会散到外网去，
所以模拟的是**跨机器**，而不是"平台自己摆的一堆假货"。4 个节点自己就是一个网络。
（用户 2026-09-20 的口径：「本机 + docker 3 个节点（共 4 个），本身可以组成网络」。）

**一个节点 = 一个身份。** 每个节点一把 ed25519 钥匙（DID = 公钥指纹，不需要谁分配），
**上架主体就是它自己的 did** —— 不再另造 `acct:alice` 这类账号。
「谁在卖」只有一个答案：卡里自证的 `x-a2n.sovereign.did` 就是它。

**上架只需要卡。** `Registry.register` 只做两件事：卡形状校验 + 本地验签，
**不检查 `url` 通不通、也不要求节点活着**（平台模式下 `require_endpoint=False`，
`url` 为空都能上架）。所以"卡在架"≠"服务活着" —— 这是真实网络的样子。

| 节点 | 身份钥匙 | 上架的卡 | 本地服务端口 |
|---|---|---|---|
| **本机节点** | `data/keys/local_node.json` | 四张 OCR 卡（见下表） | 9102 / 9103 / 9104 / 9105 |
| 容器 `a2n-market-video` | 卷 `a2n-demo-market-video:/app/state` | 短视频成片包 · 短视频口播稿 | 8787 / 8788 |
| 容器 `a2n-market-finance` | 卷 `a2n-demo-market-finance:/app/state` | 经营报表 · 合同草案 | 8787 / 8788 |
| 容器 `a2n-market-play` | 卷 `a2n-demo-market-play:/app/state` | 游戏策划案 · 商品详情页 | 8787 / 8788 |

**本机节点一次上架四张卡**（四档不一样的样本：属地 / 价目 / 结算方式 / 延迟 / 试用状态
各不相同），档位定义在 `scripts/run_a2a_node.py` 的 `PRESETS`，入口是
`scripts/run_local_node.py`：

| 端口 | 卡（展示名） | 属地 | 价目 | 结算方式 | 试用 / 模板 |
|---|---|---|---|---|---|
| 9102 | OCR 识别 · 专业版 | cn-east-2 | ¥0.03/次 | 对等账户 + 直付渠道 | 已开业（免费期走完）· 模板 v1.0 |
| 9103 | OCR 识别 · 公益版 | cn-north-1 | 免费 | 无（零准备可调） | 已开业 · **未声明模板** |
| 9104 | OCR 识别 · 极速版 | ap-southeast-1 | 0.05 USDC/次 | 只收 x402 | 已开业 · 模板 v1.0 |
| 9105 | OCR 识别 · 入门版 | cn-south-1 | ¥0.02/次 | 对等账户 + 直付渠道 | **试用中 0/10 · 免费** · 模板 v0.9-draft |

**卡上不能声明"我不走免费期"**：任何想被发现的 agent，前 10 次完成的调用一律不计费
（卡上写 `x-a2n.trial=false` 会被上架校验直接拒）。所以前 3 张卡的"已开业"不是靠一行声明
得到的 —— `sim_start.sh` 在节点起来后调 `scripts/seed_established.py`，把它们
**按真实语义**补成毕业态（消耗完免费额度 → 置毕业）。那是个命令行播种脚本，
不是任何 HTTP 路由：产品面上没有这条后门。

第 4 张卡是刻意留的：**只有它还在试用期**，控制台上的"试用中 N/10 · 免费"徽标才有真身可看。
它的草稿模板声明了 `confidence` 却还没交付 —— 于是会算出一个**非零偏差**（质量分 ≈ 85.7），
这正是硬指标该说出来的事。

钥匙持久在卷 / 文件里（重启不换身份），并且**一张卡一个稳定 uid**（由 did + 档位派生）：
重启沿用同一条上架，而不是又插一条新的。节点在**每次交付的边界上**用这把钥匙连署一份
计量（任务号 + 节点号 + 计费口径）—— 所以控制台的"计量签名"一栏会显示"已签 N/M"，
而不是永远的"未签名"。inline 交付下这一步必须落在转发边界上：那时平台代节点提交，
节点只有那一个机会。

三个**案例容器**（`docker/market_node.py`）摆的是「找 Agent」里那几档行业专家
（短视频成片包 / 口播稿 / 经营报表 / 合同草案 / 游戏策划案 / 商品详情页），
**一台容器一个身份、两份供给**（分组见 `scripts/run_market_demo_agents.py` 的
`CONTAINERS`）。控制台 → 平台 → relay 入口 → 反向隧道 → 容器内本地服务 这段走的是
真生产链路：注册、心跳、地址投影、门禁、拨号一处不少；容器**零端口映射**，
外面打不进来（全靠出站连接 + 反向隧道）。

容器里那**最后一跳真的连不上**：卡上写着"交付一份经营报表"，容器里并没有那台 agent ——
节点会**真的**去连它（默认 `http://127.0.0.1:9/invoke`，本地没人听），连不上的
**真实错误原样上报**为失败原因。所以演示里最该看的那一眼是：
**发现是通的、对方节点是活的、请求也送到了 —— 断在它转发给自己那台 agent。**
（失败原因是那次真实尝试的结果，不是一句预先写好的文案；也正因如此它绝不会被换成
`upstream 500` 这种把"参数错 / 节点坏了 / 按设计就不通"糊成一种的通用噪声。
冒烟 ⑨ 与 `check_evidence_live.py` 一起钉住这条。）

**控制台开箱身份 = 本机节点的 did**：平台按 `A2N_CONSOLE_PRINCIPAL` 把它注入
`<body data-principal>`（`sim_start.sh` 先 `run_local_node.py --print-did` 读出来再起平台）。
打开控制台看到的就是"我这台机器上架了什么"。别人的节点照样在「找 Agent」里可见可调 ——
不需要另设一个"全网视图"。

```bash
bash scripts/sim_start.sh fresh      # 清库冷启：平台 8000 + 本机节点 + 三个案例容器（需 Docker）
python scripts/a2a_smoke.py          # 端到端冒烟（发现→免费→收费→直付→对等→x402→转发失败）
python scripts/check_evidence_live.py  # 活库核验（试用/毕业/四段证据/计量签名自洽）

# 命令行走一遍发现与调用（不写 Python）
python -m a2n_sdk discover --skill ocr-pro --limit 5
python -m a2n_sdk call --principal acct:dave --agent ag_xxx --skill ocr-pro --payload '{"text":"hi"}'
```

管理台的五层体检（改 `console.html` 后按顺序跑）：

```bash
node scripts/console_js_check.js        # ① 语法：<script> 块能否编译
node scripts/console_logic_check.js     # ② 纯逻辑：价格换算/能力判定/筛选（149 项）
node scripts/ui_check.js                # ③ 渲染：真浏览器打开 /console 断言（需 playwright-core）
OUT=<dir> node scripts/console_shots.js # ④ 截图：17 张逐页非空；该有数据却空态 → exit 1
node scripts/console_polish_check.js    # ⑤ 响应式：三种视图 × 6 档宽度（2560→390）无遮挡无溢出
```

> 四层缺一不可，尤其是 ④：`innerText` 在 `display:none` 时会回退成 `textContent`，
> 所以"文本断言全过"**不等于**"这块真的显示出来了"。新 UI 必须补断言 + 补截图。

## 两个入口

| 入口 | 给谁用 | 地址 |
|---|---|---|
| **管理台** | 人：观察我提供的 / 管理我调用的 | `/console` |
| **SDK** | 机器：agent 或程序调用 agent | `packages/a2n-sdk/src/a2n_sdk` |

```python
from a2n_sdk import Node
node = Node(card, {"ocr-pro": handler}, principal="acct:bob")
node.serve()          # 注册 → 心跳 → 拉任务 → 执行 → 上报计量
```

### 本机常驻节点：当前已经接通的闭环

`a2n-node serve` 是给 AI 工作台和人工控制台共用的本机入口。它把账户、供给挂载、
使用投影、调用记录和网络观测持久化在节点自己的目录里：

```bash
a2n-node serve --home data/my-node --port 8771 \
  --bootstrap seed.example.com:9701 \
  --p2p-public-base https://my-node.example
# 控制台与工作台入口：http://127.0.0.1:8771/console
```

| 能力 | 当前语义 |
|---|---|
| 远程 A2A 长任务 | `message/send` 返回非终态 Task 后，在同一个总超时内轮询 `tasks/get`；保留远端 task/context、状态说明与 `input-required` / `auth-required`，HTTP 408/5xx 只记“交付结果未知” |
| 本机取消 | 排队任务可真正取消；运行中只进入 `CANCEL_REQUESTED`，明确标记 `remote_effect_unknown=true`；节点重启后把遗留取消请求收敛成可审计的 `INTERRUPTED`，不自动重放 |
| 资源生命周期 | 可移除使用投影、暂停/恢复/卸载供给、下架平台供给、删除未被引用的账户；暂停已发布供给会先本机停用并撤掉 P2P 广播，再在平台隐藏并停隧道，恢复时沿用原 agent id |
| 网络数据 | 导入 Agent 有后台 TCP 连接采样；P2P 邻居有签名 UDP PING/PONG RTT、收发包和异常丢弃计数，均保留最近 30 点；不执行任务、不验收、不收费 |
| P2P 发现 | 局域网 beacon + 独立重试的引导节点 + 签名 gossip；查询只接受已握手直邻，按技能轮换返回 MTU 内索引；完整 Card 只从直邻、按实测源 IP 拉取，并核对 DID、签名、哈希、技能和入口 |
| 供给广播 | 只有明确给出 `--p2p-public-base` 才广播；精确匹配的同机反向代理可读取公共 Card 并转发 A2A 调用，管理接口仍只接受本机配对；配置 URL 本身不证明公网可达 |

多跳 QUERY 目前提供的是发现线索；为避免地址替换和 SSRF，完整 Card 导入仍只接受已握手直邻
的实测来源，并不等于任意多跳目标已经可调用。这条 P2P 接线是**发现控制面**，不是完整公网
传输栈：尚无 NAT 打洞、ICE/QUIC
路径协商或去中心化自愿中继。两端都在不可互访的 NAT/CGNAT 后面时，“发现到了”仍可能
“调用不到”。平台托管结算和自持模式的免费/双边互证已经各有实现，但**通用去中心化自动
结算、多币种原子交换与跨节点争议退款仍未完成**。详见
[`docs/SDK-RUNTIME.md`](docs/SDK-RUNTIME.md)。

## 三条红线（测试锁死）

```bash
.venv/Scripts/python -m pytest tests -q
```

1. **网络侧没有任何增发路径** —— `mint` / `burn` 只允许被持牌方充值 / 打款回调触发
   （静态扫描 + 运行时调用栈双重拦截）：网络自己造不出钱，总量只随托管进出变化
2. **总量恒等于托管** —— 积分总量 ≡ 托管余额，差一分即冻结提现
3. **账本 append-only** —— 数据库触发器禁止 UPDATE / DELETE，hash 链可检测任何篡改

其余红线测试：账本无余额字段、提示价不参与排序。

## 关键设计落点

| 设计 | 代码位置 |
|---|---|
| 只刻章不碰钱 | `packages/a2n-custodian/src/a2n_custodian/` —— 唯一能对话外部资金的抽象，当前是 Mock |
| 账本不知道业务 | `packages/a2n-ledger/src/a2n_ledger/service.py` —— 只认 `ref_type` / `ref_id` |
| 分账规则可换且版本化 | `packages/a2n-kernel/src/a2n_kernel/policy.py` —— `PolicyRef(key, version, params)` |
| 发现三级漏斗 | `packages/a2n-dispatch/src/a2n_dispatch/service.py` —— 匹配 → 过滤 → 信誉排序 |
| 双向计量对账 | `packages/a2n-acceptance/src/a2n_acceptance/service.py` —— 自报 vs 平台观测 |
| 凭证链 | `packages/a2n-notary/src/a2n_notary/service.py` —— 纯订阅者，挂了只影响盖章 |

## 存储

本地用 **SQLite**（`data/a2n.db`）保证开箱即跑；生产目标为 PostgreSQL，
切换点在 `packages/a2n-store/src/a2n_store/db.py`（append-only 触发器在 PG 里用权限 + 规则实现）。

在线状态在本演示中用 `last_seen_at` 近似，生产应改用 Redis TTL。

## 连接模型：家宽电脑如何接单（v1.1 新增）

**A2N 不要求节点有公网入口。** 注册、心跳、取活、回传全部由节点出站发起：

| 模式 | 原理 | 适用 |
|---|---|---|
| `pull`（默认） | 节点长轮询取活（`GET /v1/nodes/{id}/tasks?wait=15`），有单立即返回 | 家宽 / 公司 NAT / CGNAT，零配置 |
| `wss` | 出站长连接，平台复用下发（规划中） | 同上，要求更低延迟 |
| `direct` / `relay` | 节点真有公网 URL（云主机或 frp/cloudflared），平台定期入站探测 | 低延迟场景 |

三条配套规则：

1. **NAT 判定平台替你做**：节点心跳自报本机网卡 IP，平台比对心跳来源 IP，
   回传 `nat: public | natted`。节点不需要知道自己的公网 IP。
2. **内网地址声明 direct 不认**：`127.0.0.1` / `192.168.x` 会被强制降级为 pull
   （`packages/a2n-registry/src/a2n_registry/reachability.py::normalize_connection`），防"派了单却送不进去"。
3. **发现自由，派单受控**：不可达的节点照样能被搜到（那只是信息），
   但派单前校验会拦下（`discovery.assignable`），预算不会冻在永远不会执行的任务上。

### SDK 内嵌本地管理台

```bash
.venv/Scripts/python scripts/run_node.py
# 节点常驻 + 本地管理台 http://127.0.0.1:8770（只监听 127.0.0.1）
```

本地管理台展示：节点状态、连接处境（出口 IP vs 本机 IP → NAT 判定与解释）、
**本地观测**（我侧成功率/时延——平台不知道、也不该知道的那部分）、累计收益、最近任务。
数据 = 平台 API（账目、状态）+ 进程内统计（本地观测），零第三方依赖。

```python
from a2n_sdk import Node
node = Node(card, {"ocr-pro": handler}, principal="acct:bob")
node.serve(console=True)   # console=False 关闭本地管理台
```

## 平台连接阶梯 v1.2：穿透候选 / 反向长连接 / 平台中继

核心事实：**平台有公网，"节点↔平台"永远只需要出站，不需要打洞。**
打洞只服务"两端都在 NAT 后"的场景，而 A2N v1 的调用拓扑里它不出现。
三条不可妥协的边界：

1. **打洞只做候选，永不做依赖**——对称 NAT/CGNAT 成功率低；SDK 已实现
   STUN 反射探测（RFC 5389 标准库实现），候选进入协商清单，但永不自动选中。
2. **数据可以走直连，证据必须走平台**——结果 hash、计量、分账证据仍回平台刻章。
3. **平台中继是当前保证连通的通道**——这里说的是公网平台入口 + 节点出站隧道，
   不是节点间已经实现了 TURN 或自愿 P2P 中继。

协商阶梯（ICE 思路：先列全候选，再选优；`GET /v1/nodes/{id}/transport`）：

| 通道 | 状态 | 实现 |
|---|---|---|
| `direct` | 可用 | 节点自报公网 URL + 平台入站探测 |
| `holepunch` | 候选枚举（实验） | STUN srflx 候选；平台仲裁打洞未实现 |
| `tunnel` | **可用** | 反向长连接：节点出站挂住，任务/调用实时推下来（生产 WSS，v1 长轮询下行同构） |
| `relay` | **可用（底层原语）** | 中继转发：平台公网入口 `/v1/relay/{agent_id}/*` → 隧道 → 节点本地 HTTP 服务。**不是使用端入口** —— 使用端一律走 `/v1/invoke`（治理链） |
| `pull` | 可用（兜底） | 长轮询，永远可用 |

> 边界：这里的 `holepunch` 只有 STUN 候选枚举，**真正的打洞协调与数据通道没有实现**；
> `relay` 是中心平台的底层原语。它们都不能被写成“本机 P2P 节点已经能穿透或去中心中继”。

SDK 用法：

```python
node.serve(tunnel=True)                        # 反向长连接，任务实时下发
node.serve(local_agent=(9101, local_api))      # relay：本机服务可被公网调用，本机零暴露
```

计量连署（可选，但演示节点都开着）：把 `attest_fn(task_id, node_id, dims)` 传进 `Node`，
两条交付路径（推送 / inline 转发）都会在干完活时就地签一份计量。**签名格式不在 SDK 里**
——SDK 保持零依赖，唯一口径源是 `a2n_p2p.attest.sign_metering`：

```python
import uuid
from a2n_p2p import Identity, pub_b64
from a2n_p2p.attest import card_body, sign_metering              # 唯一口径源

ident = Identity.load("data/keys/mynode.json")                # 没有就 Identity.generate().save(...)
card["x-a2n"]["uid"] = str(uuid.uuid4())                      # uid 属于签名域：自签卡必须自带
card["x-a2n"]["sovereign"] = {"did": ident.did, "pub": pub_b64(ident.pub_raw)}
card["x-a2n"]["sovereign"]["sig"] = ident.sign(card_body(card))   # 卡自证：整卡去掉 sig 再签
node = Node(card, handlers, principal="acct:bob",
            attest_fn=lambda tid, nid, dims: sign_metering(ident, task_id=tid, node_id=nid, dims=dims))
```

不给 `attest_fn` 就如实显示"未签名"——宁可说没签，也不塞一个假签名。卡上**没声明公钥**时
平台会拒收签名：签名只证明"某人签了"，归属得靠卡上的公钥，否则任何一把钥匙都能替它背书。

**卡片自证（P2）**：声明了 `sovereign` 的卡必须**用同一把钥匙签整张卡**，否则注册就被拒
（"自称了身份却验不过"）。发现路同样验这根签：验不过的卡不进任何人的列表；没声明身份的卡
放行但标 **未自证**（"在你列表里"不等于"验过了"）。`uid` 在签名域内，所以平台不会替你补写
uid —— 必须自己带上。

中继转发演示（本机不开任何端口对外）。⚠️ `/v1/relay/*` 是**底层原语**，不是使用端的第二个
调用入口：它只认调用凭据、收费语义只有对等账户，**不经门禁/建任务/验收/记账**。面向"用别人的
agent"的调用一律走 `POST /v1/invoke`（或 SDK `call_agent`）：

```bash
# ① 使用端唯一入口：治理链（门禁 → 建任务 → 执行 → 验收 → 记账）
curl -X POST http://127.0.0.1:8000/v1/invoke \
     -H "X-Principal: acct:alice" -H "Content-Type: application/json" \
     -d '{"agent_id":"<agent_id>","skill":"echo","payload":{"hello":"world"}}'

# ② 底层原语（仅对等账户/免费；做点对点自定义路由这类事才用它）
curl -X POST http://127.0.0.1:8000/v1/relay/<agent_id>/echo \
     -H "X-A2N-Call: <call-token>" -H "Content-Type: application/json" -d '{"hello":"world"}'
# → 平台 → 隧道 → 节点本机 9101 → 回包原路返回
```

工程教训（已修复并写测试）：`relay` 路由曾是 `async def` 且同步等待节点回包
15s——事件循环被阻塞，全站长轮询一起超时。阻塞等待必须 `run_in_threadpool`。

## 分包结构 v1.3：23 个可独立发布的包

单体已拆分完毕，`a2n/` 与 `sdk/` 不再存在。目录：

```
packages/
  a2n-kernel/      L0  哈希链、Merkle、领域事件、策略版本化（零依赖）
  a2n-store/       L0  schema、连接、append-only 触发器、outbox
  a2n-ledger/      L1  账本：积分真相源
  a2n-custodian/   L1  持牌方适配器（唯一能碰钱的包）
  a2n-registry/    L2  注册/发现/KYA/可达性
  a2n-transport/   L2  传输阶梯：隧道、中继、协商
  a2n-reputation/  L2  信誉（唯一可进排序的第三方事实）
  a2n-dispatch/    L3  候选集与派单校验
  a2n-acceptance/  L3  验收与双向计量对账 + 争议（仲裁入口）
  a2n-settlement/  L3  分账指令 + 每日对账 + 仲裁退款执行
  a2n-wallet/      L3  提现与销毁
  a2n-task/        L3  任务状态机与编排（对外 A2A v1.0 视图）
  a2n-notary/      L4  凭证链
  a2n-market/      L4  行情投影
  a2n-consensus/   L4  epoch 批次、Merkle 根、SPV、见证与资金锚（v1.5）
  a2n-ap2/         L4  AP2 授权世界 ⇄ A2N 结算世界的翻译层（v1.5）
  a2n-p2p/         L0  DID 身份、对等发现、gossip、可注入签名器
  a2n-account/     L2  账户与支付方式（compatible_with 是支付能力交集的唯一实现）
  a2n-gateway/     L2  门禁唯一源 gate.resolve
  a2n-deal/        L3  双边记账（bilateral）
  a2n-node/        L4  自持节点：无服务器、无托管时的发现/调用/互证（v1.6）
  a2n-server/      L5  HTTP 装配 + wiring 接线 + 管理台
  a2n-sdk/         独立 零第三方依赖
```

安装与运行：

```bash
bash scripts/install_all.sh                       # 逐个 pip install -e --no-deps
.venv/Scripts/python -m uvicorn a2n_server.app:app --port 8000
.venv/Scripts/python -m pytest tests -q           # 420 passed
```

**纪律靠机器执行，不靠自觉**（`tests/test_architecture.py`）：

1. 只能 import 自己 `pyproject.toml` 里声明过的 `a2n-*` 包 —— 未声明即判失败；
2. 依赖图必须无环（函数级 import 也算环，只是它躲到了运行时）；
3. SDK 必须零依赖；
4. 只有 `a2n-custodian` 允许出现资金系统字样；
5. `a2n-kernel` 不许长出业务概念。

迁移时真实抓到的两处架构债：registry ↔ transport 环形依赖（改为依赖注入）、
kernel 反向依赖 store 写 outbox（改为事件总线 `set_sink()` 由装配层接线）。

## 网络层 a2n-p2p v1.4：没有中心服务器的按需发现（新增包）

第 17 个包，补齐"网络是底层"里唯一完全空白的一块。它只回答网络层四个问题中的三个：

| 问题 | 实现 |
|---|---|
| Q1 我是谁 | `Identity` ed25519，`did:a2n:ag_<公钥指纹>`，私钥永不出本机 |
| Q2 我怎么找到别人 | `P2PNode` 邻居发现：broadcast beacon / bootstrap / gossip 对等交换 |
| Q3 消息怎么到 | `Envelope` gossip：**去重 + TTL + 签名**三件套 |
| Q4 我们怎么共识 | 不在本包（a2n-consensus），共识需要知道账本 |

```bash
.venv/Scripts/python scripts/p2p_demo.py
```

演示 `A ── B ── C(ocr-pro)`：**A 只认识 B，从未连过 C，却能发现它。**
这就是"注册只是允许被发现、发现是按需的、没人同步全量目录"的底层实现。

```python
from a2n_p2p import Identity, P2PNode
net = P2PNode(Identity.generate(), port=9701,
              bootstrap=[("seed.example.com", 9701)]).start()
net.announce(["ocr-pro"])                 # 广播能力
offers = net.query("ocr-pro", timeout=2)  # 按需发现（可跨多跳）
```

### 四条边界（都在测试里锁死）

1. **大负载不走 gossip** —— 签名信封限制在 1400 字节的 MTU 安全范围；完整卡、任务与
   结果都属于可靠传输层（HTTP/QUIC/tunnel）的活。
2. **网络层不传递无法验证来源的东西** —— 查不到公钥即丢弃。
3. **TTL 不参与签名** —— 它是传输属性，每跳都变，中间节点无权代表发起者重签。
   放进签名域会让任何一次转发都失效，多跳网络直接瘫痪。防放大由接收侧 clamp 保证。
4. **TOFU 学到的公钥 ≠ 被担保** —— 网络层只能证明"消息出自持私钥者"，
   不能证明"这个人可信"。**信任归管理层信誉（D5）裁定**，网络层不越界。

### 实现中抓到的三个真 bug

1. **转发污染订阅者对象** —— 就地 `decay()` 改 TTL，订阅者持有的信封被改，
   事后验签必然失败。改为克隆后 decay。
2. **查询会话号对不上** —— 用新生成的 qid，而应答回填的是 envelope 的 msg_id。
   改用报文自身的 msg_id 作为会话号。
3. **多跳应答回不来 / 回信地址可被滥用** —— 不再信任 QUERY 自报的回信地址；每一跳记录
   实际收到查询的 UDP 地址，OFFER 沿有界反向路径返回，避免成为反射放大器。

另外顺手修了架构测试自身的一个缺陷：正则 `a2n_[a-z]+` 匹配不到含数字的
`a2n_p2p`，会把它误判成未声明的包。

测试 65 个全绿（新增 21 个）。

## v1.5：把剩下的缺口一次补齐，并把所有模块衔接好

### 1. 共识层 `a2n-consensus`：锚定共识

没有中心服务器，"账本是对的"就不能由任何人单方面宣布。每个 epoch 把区间
账目压成一个 Merkle 根，**三把钥匙**集齐才定案：

| 钥匙 | 谁拿着 | 防谁 |
|---|---|---|
| 账本批次根（重算哈希链 + Merkle） | 节点 | 改账 |
| ≥2/3 见证权重签名 | 网络（WitnessSet，TOFU 只增不改） | 单点作恶 |
| 资金锚（托管余额声明签名） | 持牌方 | 凭空增发 |

任何一方单独都凑不齐三把钥匙。定案后自动向 P2P 网络广播 `ANCHOR`。
轻节点用 SPV 证明（O(log n)）即可验证"某条账目在某批已锚定的账里"，
不必同步全量 —— 家宽电脑参与核账的前提。

**测试抓到一个真 bug**：finalize 的"账本重算"最初只是重新收集存储的
hash 字段 —— 同义反复，篡改 delta 根本查不出来。已改为重算每条账目的
哈希链（delta 改 → chain_hash 不符；hash 字段改 → 断链）。

### 2. AP2 集成 `a2n-ap2`：翻译层，不是支付层

AP2 管点对点消费（授权/问责），A2N 管任务结算（验收/分账）。翻译层三条边界：
只翻译语义不碰钱；授权必须落地成**冻结**；结算凭证必须自带可验证证据。
三个扩展一一对应三个端点：

| 扩展 | 端点 | 语义 |
|---|---|---|
| 预算 | `POST /v1/ap2/budget` | Intent 授权（分）→ A2N 冻结预算（积分），1:1 无换算 |
| 凭证 | `POST /v1/ap2/receipt` | 结算 → 四件套证据（result_hash/双向计量/分账/公证章+epoch） |
| 问责 | `POST /v1/ap2/accountability` | 争议时的一页纸：自报 vs 观测 + 信誉快照 + 凭证 |

授权链（Intent→Cart→Payment）机器校验：引用闭合、主体一致、时效、
**双向金额锁死**（支付授权不得超购物车，购物车不得超授权）、可选签名验证。

### 3. A2A v1.0 状态机对齐 `a2n-task`

内部状态机显式化（`TRANSITIONS` 非法迁移直接拒绝），对外说 A2A 的话：
`GET /v1/tasks/{id}/a2a` 返回标准 Task 对象（submitted/working/completed/
failed/canceled + artifacts），A2N 特有信息全在 `metadata["x-a2n"]`。
新增 `cancel`（发起方取消，冻结原路退回）与 `fail`（节点侧失败，与
验收不通过的 REJECTED 分开记账）。

顺手修掉一个语义错误：`compute_amount` 的 `amount<=0` 兜底 1 积分已移除 ——
无可计费计量 = 计量缺失，判打回退款，绝不凭空造一笔"来源不明的 1 分钱"。

### 4. 仲裁工作台 `a2n-acceptance/dispute`

验收不通过 → 自动开争议单（system）；双方可申诉；仲裁员裁定
（uphold_reject / overturn_pay / partial）写入凭证链；退款由装配线转给
结算层**按原分账份额追回**（节点→服务费→激励池→作者池；**默认口径下钱全在节点**，
后三个池子只在 2026-09-18 之前的历史单子里可能有钱；宁记 shortfall
不透支）。管理台新增「仲裁」页可直接裁定。

### 5. 装配接线 `a2n-server/wiring.py`：所有"谁连谁"只写一次

```
events ──────────→ outbox            内核不碰存储
ledger ──────────→ consensus         共识要知道账本，但不 import 它（依赖注入）
custodian ───────→ consensus         资金锚读数来自持牌方
acceptance.failed → disputes        机器判不了的自动转人工
arbitration.resolved → settlement   裁定产出"该怎么退"，执行交给结算层
epoch.anchored ──→ p2p ANCHOR gossip 定案向网络广播
p2p offers ──────→ rosters          发现是一次网络行为，结果沉淀为本地市场列表
```

SDK 侧补齐：`sync_market()`（发现结果落本地市场文件）、`a2a_state()`
（标准 A2A 视图）、`cancel_task()`、`ap2_budget()/ap2_receipt()`。
管理台新增「仲裁 / 共识 / AP2」三个页签。

### 三层红线在新模块里的落点

- 共识层不创造事实：它只压缩与证明账本里已有的东西；
- AP2 层不碰钱：翻译进来的授权必须经 A2N 的冻结才生效；
- 仲裁不裁决技术问题：它只裁定"验收策略判得对不对"，分账执行仍走结算层。

## v1.6 自持模式 `a2n-node`：没有服务器、没有托管时的那份网络

**没有托管商（积分不上线）时，任意两个人各跑一个节点就能互相发现、互相调用 ——
不需要任何第三方。** 详见 [docs/SOVEREIGN.md](docs/SOVEREIGN.md)。

差距的落点很具体：**发现层本来就没有服务器**（`a2n-p2p` 的 gossip + 多跳按需查询），
断点在"调用"这一环 —— 平台模式下调用要走平台中继、查平台注册表、过平台门禁。
`a2n-node` 就是这三样在去中心形态下的替代品，一个包，平台侧一行没改：

| 环节 | 自持模式怎么做 |
|---|---|
| 身份 | 公钥指纹 `did:a2n:ag_<sha256(pub)[:24]>`；卡自带 `pub` + `sig`，**验卡不需要任何机构** |
| 发现 | 邻居 gossip；本次把「业务入口 + 卡哈希」随 `advert` 一起播出去，取卡可核对 |
| 调用 | 每个节点自开 HTTP 入口，请求直连对方，**自带 DID 签名**（取代明文 `X-Principal`） |
| 守门 | 门禁下沉到节点：先验身份再谈业务，受限能力只吃本地白名单 |
| 凭据 | **双边互签**：供给方签交付收据，调用方签回执引用该收据的签名，两方各存一份 |
| 台账 | 节点自己建表、自己刻章（`a2n-notary`），事实只在**自己的库**里 |
| 防重放 | `nonce` + 时间窗 |
| 钱 | 无托管 ⇒ 无积分增发 ⇒ **没有账本需要一致**（这才是"不需要服务器"的根因） |

```bash
python scripts/sovereign_demo.py    # A ── B ── C：A 只认识 B，却找到了 C 并调用它
python -m pytest tests/test_sovereign.py -q     # 34 项（单元 + 真进程集成）
```

三条纪律：**签名域必须是整份东西**（卡 = 整张卡去 sig，收据 = 整份 body）、
**did 必须由自带公钥推出**（否则"验签通过"是一句讽刺）、
**哈希/指纹/验签口径全项目只有一个实现**（自持模式与平台模式共享同一个卡哈希，
同一把钥匙不会算出两个身份）。

已知边界（不假装有）：跨公网仍需一个"会合点"、没有全局信誉、
双边互证只覆盖两方、撤回/争议/仲裁不在本模式内、密钥即身份。
