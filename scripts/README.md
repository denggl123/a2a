# 当前脚本入口

脚本是运维与验收工具，产品唯一业务入口仍由节点的 `TradeService` 和完整 SDK 承担。先看 [当前状态](../docs/PROJECT-STATUS.md)。Windows 使用仓库 `.venv\Scripts\python.exe`，服务器使用安装 SDK 的 Python 环境。

## 日常入口

| 目的 | 入口 | 是否修改现有节点 |
|---|---|---|
| 全套源码安装、自检和启动 | 根目录 `install.bat` / `install.sh`；`bootstrap.py` | 显式安装、启动 |
| 已安装产品自检、查询、停止 | `a2n-sdk doctor / request / stop` | 查询只读；停止需显式操作 |
| 项目源码/文档/安装包巡检 | `project_audit.py` | 只读，写公开摘要报告 |
| 当前四节点状态与实际代码巡检 | `project_audit.py --installed` | 只读，不调用 Agent、不付款 |
| 当前规则、版本与发行检查 | `release_check.py`；发行包加 `--artifacts --desktop --doctor --require-signed` | 自检隔离，不改业务节点 |
| 源码包 / 完整 Windows 安装包 | `build_sdk_bundle.py` / `build_desktop_bundle.py` | 更新构建产物；后者默认用本机项目发布者签名 |
| 本机网络配置 | `start_network.ps1` / 根目录 `start-network.bat` | 使用已有身份/卷，显式网络部署 |
| 保留数据升级四节点 | `deploy_local_completion.py --report-prefix <本次标识>` | 显式备份、升级和重建节点；恢复秘密只在本机加密保存 |

## 当前验收入口

| 场景 | 入口 | 范围 |
|---|---|---|
| 自动回归与模块边界 | `python -m pytest tests`、`node runtime_logic_check.js` | 临时数据，不动既有节点 |
| 隔离浏览器实际业务 | `console_acceptance.py` | 临时节点，需要 Playwright 和浏览器 |
| 三 Docker 节点实际业务 | `docker_acceptance.py` | 专用验收环境；首次建立专用环境后可作为本机节点网络 |
| 四个隔离节点发现和评分 | `selection_acceptance.py --image a2n-node-test` | 新建本次专用容器，按当前配置选择 LAN 地址；可指定 `--host` |
| 已安装四节点实际交易 | `automatic_settlement_acceptance.py` | 显式上架受控文本 Agent，首十次样品、积分/原生币/x402 和原单恢复；恢复策略后下架测试供给 |
| 已安装四节点反馈、信誉及协商 | `local_business_acceptance.py` | 受控调用和反馈；保留真实事实，不充当真人校准数据 |
| 工作流、复杂成果、独立评审 | `vision_completion_acceptance.py` | 安装真实工具，创建全新 run_id 和独立报告；`--resume 报告路径` 仅恢复原工程运行 |
| 本地测试链重启一致性 | `payment_testchain_check.py` | 显式使用本地 Anvil，无现实资金；检查原交易、余额和重放 |

已安装节点验收会产生真实样品和账目，必须明确执行。私人标签、工程标签和真人标签不得混用。巡检通过不等于执行了这些业务验收，也不等于公网验收。

## 历史与专用工具

`completion_*`、`vision_*_snapshot.py`、`r1_cross_node_check.py`、`deploy_local_points.py` 以及若干分域浏览器检查保留对应阶段的复核/迁移能力。尤其是引用 `*-2026-10-09.json` 的脚本，只针对那次历史运行，不能代替新验收；不要把它们串成新的默认部署流程。`repair_local_test_nonces.py` 只适用于文档明确的测试链迁移，不适用于普通重启或真实网络。

`free_demo_check.py` 依赖已经删除的旧平台和旧 SDK API，已删除；早期 `p2p_demo.py` 演示入口已由实际协调层与节点验收替代。既有 API 兼容入口转到同一当前实现，不维护第二套业务。
