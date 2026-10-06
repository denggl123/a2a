# 第五节点部署完成记录（公网服务器）

- **日期**：2026-10-06
- **服务器**：`172.245.148.171`（Ubuntu 24.04 LTS，Python 3.12.3，RackNerd）
- **结果**：**节点已部署上线并接入公共面**；跨公网调用链路已打通到"能收到签名回执"，
  但**测试供给的上游进程在最后一步失联，尚未跑通完整 5 节点互调**（见文末"未完成项"）。

## 一、SSH 通道（诊断结论）

服务器侧**完全正常**：`*:22` LISTEN(sshd)、`sshd_config` 无限制、
`PermitRootLogin yes` + `PasswordAuthentication yes`、ufw inactive、
iptables 全 ACCEPT、INPUT 链 0 规则、无 fail2ban、`ssh localhost` → `SSHD_ALIVE_OK`。

故障在本机：**网易 UU 加速器的 WFP 内核过滤驱动**（`uunetfilter`/`uuwfp` + `wintun`）
拦截所有非 HTTP 出站端口。`github.com:22` 同样空 banner，而 HTTP 200。
**退出加速器后 SSH 立即恢复**（拿到 `SSH-2.0-OpenSSH_9.6p1 Ubuntu-3`）；
**加速器再次启动后 22 端口又被拦**。判定流程见 `SSH-DIAGNOSIS-CHECKLIST.md`。

## 二、部署过程与证据

| 步骤 | 结果 |
|---|---|
| 构建部署包 | `artifacts/a2n-sdk-server.tar.gz`，SHA256 `d81cc1c3…`，123 文件 / 382,383 字节 |
| `--check` 校验 | `{"ok": true, "source_modified": false}` ⇒ **工作区已全部提交，源码无未提交改动** |
| 上传 | SFTP 到 `/opt/a2n/releases/incoming/`，**远端 sha256sum 与本地一致** |
| 依赖 | 缺 `python3-venv` → `apt-get install -y python3-venv` 成功 |
| 安装 | `python3 scripts/install_server.py --public-base http://172.245.148.171:8891 --public-port 8891 --coord-allow-network 172.245.148.171/32` |
| 服务 | `a2n-sdk.service` **active (running)**，`enabled`（开机自启） |
| 部署目录 | `/opt/a2n/releases/202610061030` |
| 数据 | `/var/lib/a2n/node/runtime.db`（`a2n` 用户，0600，加密） |
| 密钥 | `/etc/a2n/node.env`（`EnvironmentFile` 注入，0600） |
| 管理面 | `127.0.0.1:8771` **仅回环**（公网访问 8771 失败，符合设计） |
| 公共面 | `0.0.0.0:8891` |
| 节点身份 | **`did:a2n:ag_0c073f979153bfb578aaeebf`** |
| 公共服务 | `{"discovery": true, "samples": true, "witness": false, "task_relay": true, "blob_cache": false}`（已开任务中继） |

**今天修复的代码已在服务器上生效** —— 服务日志：
```
[node] 公开入口反代 0.0.0.0:8891 -> 127.0.0.1:8771（只放行 /a2a/* 与显式登记的 /public/* 公共协议）
READY did=did:a2n:ag_0c073f979153bfb578aaeebf local=http://127.0.0.1:8771 public=http://172.245.148.171:8891
```

## 三、安全边界实测（重要）

| 检查 | 结果 |
|---|---|
| 公网 `GET /public/v1/agents?skill=server-arith` | **HTTP 200**，返回节点 DID 与商品卡 |
| 公网 `GET /a2a/<svc>/.well-known/agent.json` | **HTTP 200** |
| 公网 `GET :8771/console` | **不可达**（管理面不回环外开放） |
| 公网 `GET :8891/v1/runtime` | **404**（反代不放行管理接口） |

## 四、已上架的真实供给

- `service_id`：**`svc_c754512be630d3769993d115`**
- 名称：服务器算术核验 / skill `server-arith` / v1.0.0
- 公开 URL：`http://172.245.148.171:8891/a2a/svc_c754512be630d3769993d115`
- 价目：前 10 次免费（`call_count` 0 元）
- 试用 notice 实测返回：
  > 初始采样的前 10 次技术交付不计费，且**一定公开为样品**（公开的是脱敏后的可公开投影；原始文件不进公开预览）。

  —— 这正是 2026-10-06 裁决的新口径，已在服务器上生效。

## 五、跨公网调用：已打通链路，真实算术结果待补

第一次跨公网调用**成功走通全链路**：

```
HTTP 200 · state=completed · a2nState=COMPLETED
network.technical_delivery=true
trade_facts: {execution: DELIVERED, free_reason: FREE_INITIAL, payment: NOT_REQUIRED, version: 1.0.0}
admission_contract: {author/provider did 一致, trade_uid 已生成, amount_minor: 0, platform_commission_minor: 0}
```

**但结果值错误**：期望 `7+5=12`，实际 `sum: 0` ⇒
**这是服务器上测试上游的 payload 解析 bug，不是平台问题**（平台侧
`upstream_status=200`、`technical_delivery=true`、签名收据齐全，都对）。

已写入更健壮的解析器（递归取 `a`/`b` + 数字兜底），但**加速器此时重新启动、SSH 再次被拦**，
无法重启该服务。

## 六、未完成项（诚实记录）

1. **测试上游需要重启**：`systemctl restart a2n-testagent`
   （当前 9000 端口不可用，导致调用返回 `UNREACHABLE`）。
2. **完整 5 节点互调未跑**：本机 4 节点（3 docker + Windows 桌面）
   `network_acceptance.py` 全 PASS，但**尚未把服务器节点接入本机发现环**
   （`pwsh -File scripts/start_network.ps1 -ServerBase http://172.245.148.171:8891 -RestartDesktop`）。
3. **跨公网 3 单真实算术结果未验证**（等 1 修好）。

**协调层 C4「跨公网双机真实驱动」仍记为未完成** —— 节点装好了不等于验收通过。

## 七、收尾指令（通道恢复后执行）

```sh
# 1) 恢复测试供给
systemctl restart a2n-testagent
curl -sS -m 8 -X POST --data-binary '{"params":{"text":"{\"a\":2,\"b\":3}"}}' http://127.0.0.1:9000/
# 期望 {"sum": 5, "node": "server"}

# 2) 本机接入服务器（第五节点）
pwsh -File scripts/start_network.ps1 -ServerBase http://172.245.148.171:8891 -RestartDesktop
python scripts/network_acceptance.py
```

> 注：服务器上的 `a2n-admin` **只有 `--path`，没有 `--method/--body`**
> （`docs/SERVER-DEPLOYMENT.md` 里的 `--method POST --body -` 是本机 CLI 的用法）。
> 服务器上要改配置，用带管理 cookie 的 curl 调回环 8771；
> token 从节点库 `runtime_control/access` 取（需 `set -a; . /etc/a2n/node.env` 注入存储密钥，
> 且 `LocalStore` 必须传 `system_protector()`）。**该文档口径需修正。**

## 八、安全提醒

- root 密码已在本对话中多次出现，**部署完成请立即更换**。
- 建议 `PasswordAuthentication no` 改密钥登录，并把 22 限制到固定来源 IP。
- 节点管理面（8771）实测已正确限制为回环，勿通过反代暴露。
