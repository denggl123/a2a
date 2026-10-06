# 第五节点 SSH 连不上：服务器侧诊断清单

- **日期**：2026-10-06
- **现象**：从开发电脑连 `172.245.148.171:22` —— TCP 连接**能建立**，但**读不到 SSH banner**（`recv` 立即返回 `b''`，干净 EOF，不是超时）。因此**与密码无关**（banner 在认证之前）。
- **对照**：`github.com:22` 经本机同一条出口同样是 `b''`；而 `http://172.245.148.171:80/` 正常返回 `HTTP/1.1 500 Domain Not Found`。
- **noVNC 实测（RackNerd 控制台，已登录）**：
  - 主机名 `racknerd-a278021`，提示符 `root@racknerd-a278021`，**已是 root 登录态**。
  - 屏幕上正在跑 `tcpdump`，抓到的是 `IP localhost.ssh > localhost.42778`，即
    **SSH 连接的源/目的都是 127.0.0.1**（本机回环），**不是公网来源**。
    `805 packets captured / 1621 received by filter / 0 dropped by kernel`，
    且可见 `PSH` 数据与递增 ack（`ack 149876` 等）⇒ **确实存在已建立的加密 SSH 会话**，
    说明 **sshd 本身工作正常**，且这些会话来自服务器**本机**，不是从公网打进来的。
  - 该系统上 **`systemctl` 命令不存在**（`systemctl: command not found`），
    并有 `ugrep` ⇒ 精简系统 / 容器式环境，服务管理不是 systemd。
- **结论**：sshd 正常、80 端口正常、公网 22 端口的连接被**建立后立即关闭**。
  故障点在**网络层**（云厂商安全组 / 主机防火墙 / 端口转发规则 / 抗攻击策略），
  不在 sshd、不在密码。

## 请在 noVNC 里逐条执行（可直接粘贴）

先说明：noVNC 里**空格键容易被吞**，且 `clear` 之类可能被 Tab 补全污染。
建议用「粘贴」按钮（noVNC 工具栏的 `Paste`）输入命令，或用 `IP=a; $IP` 这类
无空格写法代替长命令。

```sh
# 1) 实际监听情况（谁在听 22）
ss -ltnp | grep -E ':(22|2222)\b' || netstat -ltnp | grep ':22'

# 2) sshd 配置里的端口与监听地址
grep -Ei '^[[:space:]]*(Port|ListenAddress|PermitRootLogin|PasswordAuthentication)' /etc/ssh/sshd_config

# 3) 防火墙现状（ufw / firewalld / nftables 至少覆盖一个）
ufw status verbose 2>/dev/null; iptables -S 2>/dev/null | head -40; nft list ruleset 2>/dev/null | head -40

# 4) **重点**：有没有把外部 22 改道或丢弃的转发规则
iptables -t nat -S 2>/dev/null; iptables -L -n -v 2>/dev/null | grep -E '22|REJECT|DROP'

# 5) fail2ban / denyhosts 之类是否把来源封了
fail2ban-client status sshd 2>/dev/null; ls /etc/hosts.deny 2>/dev/null && cat /etc/hosts.deny

# 6) 内核层是否丢弃（SYN 收不到）
iptables -L INPUT -n -v --line-numbers 2>/dev/null

# 7) 本机回环自测：绕开网络层，证明 sshd 本身活着
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null localhost 'echo SSHD_ALIVE_OK'
```

## 判定表

| 现象 | 根因 | 处理 |
|---|---|---|
| `ss -ltnp` 里没有 22 | sshd 没起 | 起来；本机无 systemd，用 `service sshd start` 或直接 `/usr/sbin/sshd` |
| 有 22 但 `ssh localhost` 失败 | sshd 异常 | 看 `sshd` 进程与日志 |
| `ssh localhost` 成功、外部连不上 + 防火墙有 DROP/REJECT 命中 22 | 主机防火墙挡住 | 只放行本机出口 IP 的 22 |
| 上面都正常、仍连不上 | **RackNerd 上层安全组/抗攻击策略** | 提工单，或改用其他端口（见下） |

## 绕过方案（最快）

RackNerd 若在 22 上有策略，可改用**非常规端口**（需先在服务器上改 `Port` 并重启 sshd）：

```sh
sed -i 's/^#\?Port .*/Port 2022/' /etc/ssh/sshd_config
# 精简系统无 systemctl 时：pkill -HUP sshd  或  service sshd restart
ss -ltnp | grep 2022
```

改完告诉我端口，我用 `ssh -p 2022` 接上并完成节点部署。

## 已就绪（通道一通即执行）

- 部署包 `artifacts/a2n-sdk-server.tar.gz`，SHA256 `90bb5d84be7eb7e0b0575f4d657cefd79d0b7daa19443f45800e51474c71a542`，123 文件 / 383,607 字节。
- 安装：`python3 scripts/install_server.py --public-base http://172.245.148.171:8891 --public-port 8891 --coord-allow-network 172.245.148.171/32`
- 开启中继：`printf '%s' '{"services":{"task_relay":true}}' | a2n-admin --path /v1/public-services --method POST --body -`
- 验收：`python scripts/network_acceptance.py`（**五节点在线且互通才算完成**，装上不等于完成）。
- 全流程见 `docs/SERVER-DEPLOYMENT.md`。

## 注意

- 服务器密码已在对话中出现，部署完成后建议改一次。
- 本机无管理员权限，无法调整本机出站规则；此前记录的"本机拦 22"结论需修正：
  **更准确的说法是"本机出口与部分公网 22 不可用，而 80 可用；目标机 22 亦被建立后关闭"，
  两者成因可能不同，需以上面第 3/4/6 条命令结果为准。**
