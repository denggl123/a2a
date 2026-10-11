# 本地支付测试快照与恢复规则

更新：2026-10-11，0.2.0b7。本工具只用于现有本地 Anvil 测试链及测试 facilitator。

## 范围与数据归属

- 链状态与 facilitator 收据属于持久测试环境；SDK 升级不会重置它们。
- 快照是现有状态的只读备份，不产生支付、发行积分或自动重试交易。
- 两种服务使用不同接口：Anvil 使用 anvil_dumpState；facilitator 使用容器内 public-test-state.json，不向 18946 发送 Anvil RPC。
- 默认备份位于当前节点 home 下的 snapshots；支持 --backup-root 指定其他目录或磁盘。默认目录不承诺独立磁盘或独立故障域。
- 快照写入临时文件，完整内容校验后 fsync、原子替换；Linux 同时同步目录。文件名含纳秒标识，避免同秒调用覆盖；仅轮转相同类型的完整备份，默认保留五份。
- 失败退出码非零。读取失败、损伤、非法 JSON、gzip CRC 错误或落盘失败不会用坏内容替换已有备份。

## 创建快照

在已安装 SDK 的 Python 环境中运行：

~~~text
python scripts/anvil_snapshot.py --once --backup-root data/snapshots --keep 5
python scripts/facilitator_snapshot.py --backup-root data/snapshots --keep 5
~~~

链 RPC 默认 http://127.0.0.1:18945，可通过 --rpc 修改；facilitator 容器默认 a2n-payment-testfacilitator，可通过 --container 修改。两个脚本只执行一轮，不创建后台进程或计划任务。

输出分别为 testchain/testchain-*.json.gz 与 facilitator/facilitator-*.json。链快照接受 RPC 返回的原始 JSON 或 gzip 十六进制内容，完整解析后重新写入规范 gzip。压缩前后均有 64 MiB 内容额度。

## 恢复规则

恢复会改变测试链状态，必须由操作者明确执行；脚本不自动选择备份或回滚账目。

1. 记录链 ID、块高、token 地址、已有交易哈希和 SDK 未明订单；保存当前损伤文件。
2. 暂停对应测试环境的交易调用，停止 testchain 和 facilitator。不要停其他业务节点。
3. 完整解压并解析拟用链快照；完整解析拟用 facilitator JSON。选择对应同一测试链的两份状态。
4. 将解压后的链 JSON 放回 testchain 的 /state/chain.json，将 facilitator JSON 放回 /state/public-test-state.json。
5. 启动测试链，再启动 facilitator；核对链 ID、块高、token 链上代码、原交易收据和 SDK 账目。
6. 发现收据或链不匹配时保留未知状态，不重发原交易，不把缺失收据当作付款成功。
7. 核对通过后创建新的快照，恢复测试调用。

不使用旧备份覆盖仍在写入的文件；不把任意最新的两份文件当作一致性事务快照。两脚本不共享事务锁，因此恢复时还需核对原收据及链事实。

## 容器周期写盘

testchain.yaml 的周期写盘间隔为 60 秒，移除历史态快照参数；这些设置在显式重建测试链容器后生效。外部快照可以减少单个周期文件损伤造成的影响，但没有安排调度时不能宣称自动备份已经运行。此轮不会为应用自行创建定时任务，也不自动重建或清空既有测试链卷。

## 验证范围

单元回归验证完整 gzip/JSON、内容额度、原子落盘失败保留旧文件、唯一命名及轮转边界。现有环境另外执行只读快照验证；该结果与破坏性恢复演练分开记录。早期人工演练信息保留在 Git 历史中，不作为本次自动恢复承诺。
