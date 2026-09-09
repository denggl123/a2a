"""调用凭据（call token）：A2N 的门禁。

为什么需要它：使用方从发现结果里拿到的是 A2N 的中继入口
（/v1/relay/{agent_id}），不是节点的真实地址。但中继入口本身是公网的——
如果谁都能打，等于拿 A2N 当免费的公网跳板，还能绕过计量与对账直接蹭节点。

规则很简单：
- **签发**：只给"与该 agent 存在 ACTIVE 对等账户配对"的使用方签发（一期权限边界）；
- **校验**：中继转发前必须出示凭据，验 HMAC、验过期、验 agent 绑定；
- 节点侧零信任成本：请求能从隧道进来，必然已经过了平台这道门——
  因为隧道是节点自己主动连出去的长轮询，外面的人够不着那条通道。

secret 默认进程内随机生成；多副本部署时用环境变量 A2N_CALL_SECRET 共享。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

_SECRET = None
DEFAULT_TTL = 600  # 秒。凭据是短命的：够打完一次调用就行


def _secret() -> bytes:
    global _SECRET
    if _SECRET is None:
        env = os.environ.get("A2N_CALL_SECRET")
        _SECRET = env.encode() if env else secrets.token_bytes(32)
    return _SECRET


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload: bytes) -> str:
    return _b64(hmac.new(_secret(), payload, hashlib.sha256).digest())


def issue(agent_id: str, principal: str, ttl: int = DEFAULT_TTL) -> str:
    """签发一张只对 agent_id 有效、到期作废的调用凭据。"""
    payload = json.dumps({
        "agent_id": agent_id, "principal": principal,
        "exp": int(time.time()) + int(ttl), "nonce": secrets.token_hex(8),
    }, ensure_ascii=False, sort_keys=True).encode()
    return _b64(payload) + "." + _sign(payload)


def verify(token: str, agent_id: str) -> tuple[bool, str]:
    """验签 + 验过期 + 验 agent 绑定。任何一关不过都进不来。"""
    try:
        payload_b64, sig = token.split(".", 1)
    except ValueError:
        return False, "凭据格式不对"
    payload = _unb64(payload_b64)
    if not hmac.compare_digest(_sign(payload), sig):
        return False, "凭据签名无效"
    try:
        data = json.loads(payload)
    except ValueError:
        return False, "凭据内容无法解析"
    if data.get("agent_id") != agent_id:
        return False, "凭据与该 agent 不绑定"
    if int(data.get("exp", 0)) < time.time():
        return False, "凭据已过期"
    return True, "ok"


def subject(token: str) -> str | None:
    """凭据里是谁（校验通过后用于审计/透传）。"""
    try:
        return json.loads(_unb64(token.split(".", 1)[0])).get("principal")
    except Exception:  # noqa: BLE001
        return None
