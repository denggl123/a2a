# 完整 SDK 的 Linux 服务安装

该入口安装同一套 5 个包和同一节点运行时。服务器上只运行一个 SDK 节点，不另建中央平台。

## 安装包

在开发电脑运行 `python scripts/build_sdk_bundle.py`，生成 `artifacts/a2n-sdk-server.tar.gz` 和 SHA256 清单。包中只有当前 SDK 源码和安装入口，不含本机数据库、节点存储密钥或 SSH 凭据。

发布前运行 `python scripts/build_sdk_bundle.py --check`，同时核对包内每个文件、当前源码和清单。源码变更后旧包会被拒绝。清单记录 Git 提交、是否含未提交修改、源码总哈希及每个文件的哈希；未提交修复不能仅凭提交号判断版本。

服务器需 Python 3.11+、venv 和 systemd。将安装包解压到独立目录，例如 `/opt/a2n/releases/20261005`，以 root 执行：

```sh
python3 scripts/install_server.py \
  --public-base http://172.245.148.171:8891 \
  --public-port 8891 \
  --coord-allow-network 172.245.148.171/32
```

上述 HTTP 地址用于明确授权的试点网络；其他节点同样需要显式允许该服务器 `/32`。正式公共入口配置为具有有效证书的 HTTPS origin；已有 nginx 可将这个 origin 的公共协议路径转发到 8891。

## 服务与数据

- 服务：`a2n-sdk.service`，专用 `a2n` 用户，开机启动、失败重启。
- 业务数据：`/var/lib/a2n/node/runtime.db`，AES-GCM 加密，升级沿用身份和数据。
- 密钥及节点参数：`/etc/a2n/node.env`，root 权限 0600，已有存储密钥沿用。
- 管理入口：回环 `127.0.0.1:8771`；公共代理仅开放公共协议，管理接口和控制台不在公共端口开放。
- 本机管理：`a2n-admin --path /v1/runtime`（**只读**）。
  写操作要按部署位置分两种情况：
  - **本机 CLI**（开发电脑的 `.venv`）：支持 `--method POST --body -`。
  - **服务器上的 `a2n-admin` 只有 `--path`**（实测 2026-10-06，`--help` 只有
    `--home/--port`），提交操作要走回环管理接口：先从节点库
    `runtime_control/access` 取 `management_token` 当 Cookie（Linux 需
    `set -a; . /etc/a2n/node.env` 注入 `A2N_STORAGE_KEY`，且 `LocalStore`
    必须传 `system_protector()`），再
    `curl -X POST -H 'Cookie: A2N_LOCAL_TOKEN=…' --data-binary @body.json http://127.0.0.1:8771/v1/...`。
    管理面只认这个 token 或配对码换来的会话，直接 curl 无 token 会得 401。
- 检查：`systemctl status a2n-sdk`、`journalctl -u a2n-sdk`。

安装入口不修改现有 SSH、nginx 或主机防火墙。部署时检查 8891 的可达性并按实际主机规则开放该公共端口。部署成功以服务就绪、身份返回及真实跨节点验收为准，安装包生成不等于服务器已安装。

## 本机四节点入口

`start-network.bat` 启动已有三个 Docker 节点和本机完整 SDK。可显式指定 `-HostAddress 192.168.31.21`；已有桌面节点使用其他配置时加 `-RestartDesktop` 正常重启，身份和交易记录保留。

桌面网络设置同时保存在实际节点目录的 `network.json`，SDK 启动和计划任务统一读取它；`.tmp/network-settings.json` 保存网络启动入口的选择。当前 Windows 环境的用户目录在计划任务与当前会话之间存在文件视图差异，因此通过仓库 `.a2n-local.json` 指定共享的 `data/desktop-sdk`。计划任务直接运行项目 Python，无需经过 Git Bash；看护循环会在节点退出后恢复同一身份。原用户目录与旧节点目录保留，禁止在其他入口继续启动旧身份。

本机公共端口为 18881、18882、18883、18885，管理面保持回环。本机四节点发现环是 `A → B → C → Desktop → A`。运行 `python scripts/network_acceptance.py` 验证每两个不同节点之间的发现、真实 HTTP 供给调用与签名收据，结果写入 `artifacts/network-local.json`。

该启动配置使用当前局域网地址。DHCP 地址改变后应重新运行启动入口更新公开地址；该局域网配置还不能证明服务器已经加入，也不能代替跨公网或 NAT 的接入验证。

## 第五节点接入和验收

服务器安装成功、公共端口确实可达后，通过服务器的本机管理接口开启可选密封任务中继：

```sh
printf '%s' '{"services":{"task_relay":true}}' | \
  a2n-admin --path /v1/public-services --method POST --body -
```

在开发电脑应用服务器入口：

```powershell
pwsh -File scripts/start_network.ps1 -ServerBase http://172.245.148.171:8891 -RestartDesktop
```

三个 Docker 节点与桌面 SDK 保留局域网入口和发现环，同时主动向服务器注册协调邮箱和密封供给。HTTP 试点只额外信任该服务器 IPv4 `/32`；HTTPS 域名不放宽证书验证。设置保存在 `.tmp/network-settings.json`，以后双击启动入口继续使用。恢复四节点配置用 `-LocalOnly -RestartDesktop`。启动提示只表示参数已应用，五节点在线与互通须以实际验收为准。

验收时另外在服务器回环运行 HTTP 算术上游；此测试供给不随普通 SDK 安装加载。将仓库的 `tests/docker/agents.py` 单独传到服务器后，用 `A2N_TEST_AGENT_HOST=127.0.0.1` 启动；默认测试端口为 9000，可通过 `A2N_TEST_AGENT_PORT` 更换。服务器上的真实 Agent 也可以提供相同 `{sum: a+b}` HTTP 协议用于本轮验证。

远端管理接口始终保留在回环，通过稳定的 SSH 管理通道访问。`--server-admin` 是 JSON 参数数组，脚本直接创建进程，不经过本机 shell；该命令需接受与 `a2n-admin` 相同的参数、JSON 标准输入，且只将 API JSON 写入标准输出。例如已有 SSH 密钥认证时：

```powershell
$serverAdmin = '["ssh","-o","BatchMode=yes","root@172.245.148.171","/usr/local/bin/a2n-admin"]'
.venv\Scripts\python.exe scripts/network_acceptance.py `
  --server-admin $serverAdmin --server-upstream http://127.0.0.1:9000/invoke
```

此模式检查 5 个不同 DID、强制发现、实际 API 上架、20 个有向买卖组合及收据签名、双方 DID 和输出哈希。服务器调用四个局域网供给时必须选中密封中继卡。75 项检查全部通过才产生成功的 `artifacts/network-five.json`；失败时保留已有事实、失败步骤和错误。不带服务器参数仍运行四节点的 45 项检查，写入 `network-local.json`，不会计作公网验证。
