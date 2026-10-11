# 发布规范与自动验收

## 版本、许可与文档

根目录 `VERSION` 是唯一产品版本，五个 Python 包、SDK 运行时、源码包和 Windows 安装包均须一致。当前为 `0.2.0b2`，属于 Beta 预发布。更改版本时同时更新五份包元数据、`a2n_sdk/version.py` 和变更记录；`release_check.py` 会拒绝遗漏。

项目代码使用 [Apache-2.0](../LICENSE)，[NOTICE](../NOTICE) 说明依赖、用户内容及外部 Agent 的许可不随本项目改动。当前文档统一从 [文档入口](README.md) 导航；历史设计保留但不作为当前兼容或完成率承诺。安全问题及真实资金限制见 [SECURITY](../SECURITY.md)。

## 自动验收流水线

`.github/workflows/acceptance.yml` 在推送、PR 和人工触发时运行：

1. Windows 与 Linux/Python 的完整测试，包含真实私有 EVM 与积分/MCP 两节点交易。
2. 前端逻辑校验，临时隔离节点的真实浏览器控制台验收。
3. 元数据、许可证、当前文档链接及源码包完整性检查。
4. Windows 完整安装包候选构建，独立执行 doctor，核对嵌入版本、源码摘要和实际可执行文件摘要。

远程 CI 尚须在 GitHub 上实际触发才有远程运行结果；本机验收报告与远程 CI 状态分开。测试使用随机身份、临时 home 和测试链，不访问既有业务节点或主网资金。

## 本机构建与核对

```text
python scripts/release_check.py
python -m pytest tests -q
node scripts/runtime_logic_check.js
python scripts/console_acceptance.py
python scripts/build_sdk_bundle.py
python scripts/build_desktop_bundle.py
python scripts/release_check.py --artifacts --desktop --doctor --require-signed
```

开发前按贡献指南安装测试链依赖与指定构建器。浏览器验收要求 Node.js、playwright-core 和可用 Chromium，允许通过 `NODE_EXE`、`NODE_PATH`、`A2N_CHROME` 指定运行位置。

源码清单明确排除本机 data、数据库、钱包、环境变量文件和发布者私钥。文本行尾先规范化，再进行逐文件摘要和源码整体摘要；gzip/tar 的时间、属主及顺序固定，相同源码生成相同压缩包。manifest 记录真实 Git 提交和工作区是否修改，不将修改过的产物冒充该提交。

Windows 包嵌入同一版本/源码摘要并包含五包运行时、控制台、项目许可和第三方许可证清单。源码在构建过程中改变会失败。doctor 在独立临时节点实际启动并检查身份、存储和核心装配。源代码摘要、文件摘要、签名和运行结果均匹配后才通过发布检查。

## 签名与公开分发

本机构建默认复用 `data/release-publisher` 中的持久发布者身份，生成 Ed25519 项目签名。私钥和节点数据永不上传。项目签名不是 Windows 系统发行证书。使用者必须通过独立可信渠道确认发布者 DID；仅把同一个下载文件内的公钥当作信任依据不够。

CI 使用 `--unsigned` 构建候选，绝不生成或下载正式发布者私钥。候选只作为 workflow artifact。正式版本在可信本机重建、完整验收和持久身份签名后，由维护者发布；`require-signed` 拒绝未签名候选。工作流不会自动创建 GitHub Release 或发布到 PyPI。

公开文件至少包含：A2N.exe、对应 `.release.json` 与 desktop manifest、服务器源码包及 manifest、SHA256SUMS、当前规则/变更记录及验收摘要。不得上传 data、doctor 临时身份、钱包或工作区私人配置。升级既有节点前核对原身份和备份，升级后读取该节点实际 runtime 版本；构建通过不等于运行中的旧安装包已升级。
