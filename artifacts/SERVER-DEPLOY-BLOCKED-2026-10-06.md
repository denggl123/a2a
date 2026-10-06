# 第五节点（服务器）部署受阻记录

- **日期**：2026-10-06
- **目标**：把 A2N 节点部署到公网服务器 `172.245.148.171:22`（root），与本机 Windows 节点 + 3 个 Docker 节点组成 5 节点网络。
- **结论**：**未部署成功**。原因是本机网络出口不允许 SSH（22 端口）出站，不是服务器故障，也不是凭据问题。以下是可复核的诊断证据。

## 一、目标机是活的

| 探测 | 结果 |
|---|---|
| `http://172.245.148.171:80/` 经本地代理 | **HTTP 200**，响应头 `Server: nginx`，`Last-Modified: 2026-07-09` |
| `/index.html` | HTTP 200 |
| `https://…:443 / :8443 / :4443` | 不通（curl schannel 错误或超时） |

⇒ 目标机在线、nginx 在跑、80 端口对外可达。

## 二、SSH（22）拿不到 banner

| 通路 | 目标机 22 | 对照 github.com:22 |
|---|---|---|
| 本机直连（`dangerouslyDisableSandbox`） | TCP 通，banner `b''` | banner `b''` |
| HTTP CONNECT 隧道（`127.0.0.1:7892`） | CONNECT 200，隧道内 EOF | — |
| SOCKS5（同一代理，`0500` 认证通过） | 连接建立，banner `b''` | **banner `b''`** |
| OpenSSH + `ProxyCommand`(SOCKS5) | `Connection closed by UNKNOWN port 65535` | — |

**决定性对照**：`github.com:22` 是公开正常服务，经同一 SOCKS5 代理同样返回空 banner。
⇒ 问题在**本机出口**，不在目标服务器。

## 三、本机出网环境

- 直连公网被拦：`github.com:443` 超时、`172.245.148.171:80/443` 直连超时。
- 唯一可用出口：本地代理 `http://127.0.0.1:7892`（**注意端口是 7892，不是记忆里旧记的 7890**；
  `7890/7891/7893` 均关闭）。经它 `curl https://github.com` = 200。
- 该代理对**任意端口**都回 `200 Connection established`，但不真正透传非 HTTP 流量 ——
  所以"端口扫描"结果全是 200，**不可作为端口判据**（当时据此得出过错误结论，已纠正）。
- 代理**同时支持 SOCKS5**（`\x05\x01\x00` → `0500`），但 22 端口流量仍被丢。

## 四、已就绪的部署资产（通道一通即可执行）

- 部署包已构建：`artifacts/a2n-sdk-server.tar.gz`
  - SHA256 `90bb5d84be7eb7e0b0575f4d657cefd79d0b7daa19443f45800e51474c71a542`
  - 123 文件 / 383,607 字节 / 5 个包 / `source_modified: true`（构建时工作区有未提交改动）
- 标准流程见 `docs/SERVER-DEPLOYMENT.md`（该文档已用本机 IP 举例，公共端口 **8891**）：

```sh
# 服务器上（root，Python 3.11+）
python3 scripts/install_server.py \
  --public-base http://172.245.148.171:8891 \
  --public-port 8891 \
  --coord-allow-network 172.245.148.171/32
# 开启可选密封任务中继
printf '%s' '{"services":{"task_relay":true}}' | \
  a2n-admin --path /v1/public-services --method POST --body -
systemctl status a2n-sdk
```

```powershell
# 本机接入第五节点
pwsh -File scripts/start_network.ps1 -ServerBase http://172.245.148.171:8891 -RestartDesktop
python scripts/network_acceptance.py    # 验收：不是"装上了"，是"真能互相发现调用"
```

## 五、需要用户提供什么（二选一）

1. **放行本机出口到 22**：本机当前走代理出网，代理需允许 22 端口 TCP 透传；或给一个可直连的 SSH 通道。
2. **改用其他管理通道**：云厂商 VNC/网页控制台（可切 root、改 sshd 端口、放行安全组），
   或临时在 nginx 上开一个受控入口。

## 六、验收口径（不许提前说成功）

第五节点**在线且互通**必须以 `network_acceptance.py` 实跑通过为准，
而不是"包构建出来了"或"服务装上了"。这正是协调层 C4 长期缺的
「跨公网双机真实驱动」证据 —— 拿到它之前，这一条一直记为**未完成**。
