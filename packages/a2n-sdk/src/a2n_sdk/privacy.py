"""公共内容投影：样品与反馈共用的脱敏工具，无业务或网络依赖。"""
from __future__ import annotations

import re
from typing import Any

_SECRET_SUBSTR = ("password", "passwd", "secret", "token", "apikey", "api_key",
                  "access_key", "private_key", "credential", "authorization",
                  "cookie", "email", "phone", "mobile", "ssn", "idcard",
                  "id_card", "passport",
                  "密码", "密钥", "令牌", "身份证", "手机号", "邮箱",
                  "私钥", "签名")
_SECRET_EXACT = {"sig", "key", "auth", "pwd", "secret", "sk", "pk"}
_REDACTED = "[已隐去]"

# 身份与句柄键：不属于"密钥"，但**同样不许进公开投影**。
# 漏了它们的真实代价：公开样品里带出 `asset_id` + `owner_did`，
# 任何拿到样品的人都能拿这个句柄去 `a2n-assets/1` 索取别人的私有文件 ——
# 脱敏在这里不是"体面"，是权限边界。
_IDENTITY_SUBSTR = ("owner_did", "requester_did", "caller_did", "provider_did",
                    "counterparty_did", "recipient_did", "relay_did", "buyer_did",
                    "asset_id", "lease_id", "session_id", "grn_id")
_DID_PATTERN = re.compile(r"did:a2n:[a-z]+_[0-9a-f]{16,}")

# 自由文本里的敏感**内容**：只按字段名隐去键是不够的 —— 需求摘要、交付正文这类
# 自由文本里，邮箱、手机号、API Key 仍可能明文带出来。这里做模式级兜底。
_TEXT_PATTERNS = (
    ("私钥文件", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                          re.S)),
    ("访问密钥", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("API 密钥", re.compile(r"(?i)\b(?:sk|pk|rk|ghp|gho|ghu|ghs|xox[baprs])[-_][A-Za-z0-9]{12,}")),
    ("邮箱", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("键值密钥", re.compile(
        r"(?i)[\"']?\b(?:api[_-]?key|access[_-]?key|secret|token|password|passwd|pwd)\b[\"']?"
        r"\s*[:=]\s*(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;\"']+)")),
    # 自由文本里出现的 did 同样是身份，不该随摘要一起公开。
    ("身份标识", _DID_PATTERN),
)


def scrub_text(text: Any, removed: list) -> Any:
    """把自由文本里的敏感内容替换成 `[已隐去]`，并把命中的类别记进 `removed`。"""
    if not isinstance(text, str) or not text:
        return text
    out = text
    for label, pattern in _TEXT_PATTERNS:
        if pattern.search(out):
            out = pattern.sub(_REDACTED, out)
            removed.append(label)
    return out



def is_secret(key: Any) -> bool:
    text = str(key or "").lower()
    return (text in _SECRET_EXACT
            or any(part in text for part in _SECRET_SUBSTR)
            or any(part in text for part in _IDENTITY_SUBSTR))


def redact(value: Any, removed: list) -> Any:
    """递归投影：隐去疑似密钥/隐私，并记下移掉了什么。

    两道防线：
    * **按字段名**（`is_secret`）—— 命中就把整条键值一起换成 `[已隐去]`；
    * **按内容模式**（`scrub_text`）—— 自由文本里的邮箱、手机号、API Key、
      私钥等照样替换。只按字段名会漏掉"需求摘要""交付正文"这类自由文本里的明文。
    保守起见不去猜模糊内容（会把正常成品也删掉）；真要更强判断，由调用方在
    `hidden_reason` 里如实说明，而不是静默隐藏。
    """
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if is_secret(key):
                out[str(key)] = _REDACTED
                removed.append(str(key))
            else:
                out[str(key)] = redact(item, removed)
        return out
    if isinstance(value, list):
        return [redact(v, removed) for v in value]
    if isinstance(value, str):
        return scrub_text(value, removed)
    return value

