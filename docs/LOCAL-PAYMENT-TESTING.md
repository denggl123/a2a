# 本地支付测试

使用官方 Foundry Anvil 本地 EVM，而不是下载陌生钱包后试付真实币。官方项目：<https://github.com/foundry-rs/foundry>，Anvil：<https://www.getfoundry.sh/anvil/index.html>。

本轮固定官方镜像版本 v1.8.5 和镜像摘要。链号为 `eip155:31337`，测试原生币为 TETH。节点通过本机受保护接口生成随机钱包，私钥沿用既有加密存储，不采用公开的 Anvil 默认私钥。重复初始化不换钱包，有既有钱包时拒绝覆盖。

## 本机环境

- 测试链：`a2n-payment-testchain`，Windows RPC `http://127.0.0.1:18945`，Docker 节点 RPC `http://a2n-payment-testchain:8545`。
- x402 测试收款端：`a2n-payment-testfacilitator`，Windows `http://127.0.0.1:18946`，Docker `http://a2n-payment-testfacilitator:8546`。
- 两个持久卷保存测试链和收据；账户页显示本地测试身份。没有货币价值，不连接主网。
- 常驻四节点现已配置测试收付款通道和有限测试预算。原生币与 EIP-3009 测试代币仍由支付协调层选择，积分模块继续独立使用。

账户页“创建测试钱包”需要先运行本地链。接口为 `POST /v1/payment-coordination/test-wallet`，只有 `rpc_url` 一个字段；验证本地地址、Anvil 客户端和固定链号后才建立钱包并发放测试余额。

`docker/testchain.yaml` 同时定义固定版本测试链和常驻 facilitator；`docker/Dockerfile.payment-test` 和 `examples/payments/anvil_facilitator.py` 提供后者的实现。测试代币允许公开铸币，使用本地链的公开管理员账户，不能用于真实网络。

重新安装测试环境时，先建立既有节点网络和基础节点镜像 `a2n-node-test`，再创建两个持久卷并启动。已存在的卷会继续保留原链和收据：

```powershell
docker volume create a2n_payment_testchain_v2
docker volume create a2n_payment_testfacilitator_v2
docker compose -f docker/testchain.yaml up -d --build
```

测试链持久卷与 facilitator 持久卷必须配套保留。只重建链而沿用旧收据/代币地址时，服务会拒绝错误状态，不把旧记录解释为新链付款。测试 RPC 和铸币接口只绑定本机回环地址。

### 重启与旧环境迁移

2026-10-11 查明旧 Anvil 容器使用 uid 1000，对 root 所有的卷没有写权限，链快照未落盘。旧链已丢失的状态不能从 facilitator 收据重建。旧容器改为 `*-legacy-20261011` 并停止自动重启，原来的两个卷完整保留；新 v2 卷配对建立，不复用旧收据。新测试代币由不同的公开测试账户部署，地址与旧代币不同。节点的历史账目与原通道保留，不把历史付款重新认作本次已付款。

当前仅测试链容器以 root 写入自己的测试卷；Anvil 每五秒保存完整历史快照。facilitator 在部署、铸币、代币结算及模拟丢响应之前额外落盘并同步写入，先保存链快照再保存收据。启动等待 RPC 就绪，并逐笔检查缓存收据在该链上真实成功。验证重启应使用 `scripts/payment_testchain_check.py`，检查真实 token 转账、收据、余额与同笔重放不重复付款。

实际节点验收入口：`scripts/vision_completion_acceptance.py`。它安装有实际功能的工作流、验证前十次零结算、原生币付款、x402 代币到账、双方协商退款、同笔重试和私有成果传输。验收记录标为受控用途，不计入三天真实校准，也不代表独立用户认可。

真实钱包可通过既有加密 keystore 配置入口导入。切换钱包或配置真实原生币通道后，不再显示本地测试标签；未完成主网资金验收。
