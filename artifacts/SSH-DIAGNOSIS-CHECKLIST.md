# 第五节点 SSH 连不上：诊断结论与解法

- **日期**：2026-10-06
- **目标**：`172.245.148.171:22`，root。

## 一、最终结论：服务器完全正常，问题在本机

### 服务器侧证据（noVNC 实测，用户执行）
- `ss -ltnp`：`LISTEN 0 4096 *:22` users=`sshd`(pid 843)、`systemd`(pid 1) ⇒ **sshd 在所有接口上正常监听 22**。
- `grep Port|ListenAddress /etc/ssh/sshd_config` ⇒ **无自定义 Port、无 ListenAddress 限制**。
- `PermitRootLogin yes` / `PasswordAuthentication yes` ⇒ **允许 root 密码登录**。
- `ufw status` ⇒ `Status: inactive`；`iptables -S` ⇒ 全是 `-P ... ACCEPT`，**无任何拦截规则**。
- `iptables -L INPUT -n -v` ⇒ `policy ACCEPT 0 packets 0 bytes`，**零规则零计数**。
- `iptables -t nat -S` ⇒ 无 NAT 改道规则。
- `fail2ban-client` 不存在；`/etc/hosts.deny` 全部是注释 ⇒ **无封禁**。
- `ssh localhost 'echo SSHD_ALIVE_OK'` ⇒ **输出 `SSHD_ALIVE_OK`** ⇒ sshd 功能完好。

**结论：服务器侧无任何问题，sshd 健康。**

### 本机侧证据（决定性）
| 探测 | 结果 |
|---|---|
| 直连 `github.com:22` | **空 banner（立即关闭）** |
| 直连 `gitlab.com:22` | **空 banner（立即关闭）** |
| 直连 `172.245.148.171:22` | **空 banner（立即关闭）** |
| 直连/代理访问 `https://github.com` | **HTTP 200** |
| 直连目标机 21/25/80/1080/5000/8080/8891/9090 | 全部超时或立即关闭（无区分） |
| 6 次重复采样 22 与 80 | 6/6 稳定复现，**结果不波动** |

**⇒ 本机只放行 HTTP/HTTPS，所有其他出站 TCP 端口被拦。与目标服务器无关，与密码无关。**

### 元凶（本机内核级网络过滤）
- 占用代理端口 `127.0.0.1:7892` 的进程是 **`xxyunCore.exe`**（PID 27004），不是 Clash。
- 系统装有 **网易 UU 加速器的 WFP 过滤驱动**：
  - `uunetfilter` — "UU NF WFP Driver"（内核）
  - `uuwfp` — "UU WFP Driver"（内核）
  - `wintun` — 虚拟网卡
- **WFP（Windows Filtering Platform）是内核级网络过滤**，可按端口/协议拦截，
  且这些驱动以服务态运行、优先级高于用户态防火墙规则 —— 所以
  `Disable-NetFirewallRule` / `New-NetFirewallRule` 全部"拒绝访问"也改不动它
  （本机 `IsAdmin=False`，且这些是驱动层不是规则层）。
- 另有系统级持久规则 `codex_sandbox_offline_block_outbound`（封非回环出站），
  同样 `IsAdmin=False` 无法关闭。

## 二、解法（按推荐顺序）

### 方案 1：退出 UU 加速器（最直接）
完全退出网易 UU 加速器（托盘退出，不是最小化），必要时在"设置 → 网络加速"里关闭
其驱动加载。退出后本机出站 TCP 即恢复，然后：

```sh
ssh -p 22 root@172.245.148.171
```

### 方案 2：换网络出口
用**手机热点**或另一条不经过本机加速器/WFP 的网络，再试 SSH。同一台服务器、同一密码。

### 方案 3：在服务器上开非常规端口（若怀疑特定端口策略）
```sh
sed -i 's/^#\?Port .*/Port 2022/' /etc/ssh/sshd_config
pkill -HUP sshd        # 精简系统无 systemctl
ss -ltnp | grep 2022
```
但注意：本次实测显示本机对**所有**非 HTTP 端口都拦，**换端口大概率无效**——
除非退出加速器后 22 仍不通，才值得试这条。

## 三、通道打通后立刻执行的部署步骤

- 部署包已就绪：`artifacts/a2n-sdk-server.tar.gz`
  （SHA256 `90bb5d84be7eb7e0b0575f4d657cefd79d0b7daa19443f45800e51474c71a542`，123 文件 / 383,607 字节）
- 安装：`python3 scripts/install_server.py --public-base http://172.245.148.171:8891 --public-port 8891 --coord-allow-network 172.245.148.171/32`
- 开启中继：`printf '%s' '{"services":{"task_relay":true}}' | a2n-admin --path /v1/public-services --method POST --body -`
- 本机接入：`pwsh -File scripts/start_network.ps1 -ServerBase http://172.245.148.171:8891 -RestartDesktop`
- 验收：`python scripts/network_acceptance.py`
  （**五节点在线且互通才算完成**；装上 ≠ 完成，脚本通过才算。）
- 全流程见 `docs/SERVER-DEPLOYMENT.md`。

## 四、安全提醒
- root 密码已在本对话中出现过，部署完成后建议立即更换。
- 建议部署时限制 sshd 只允许密钥登录（`PasswordAuthentication no`），
  或在安全组中把 22 限制到固定来源 IP。
