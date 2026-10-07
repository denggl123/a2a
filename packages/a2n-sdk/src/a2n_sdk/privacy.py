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
# 句柄会暴露私有交付的关联关系；文件读取本身仍须通过买方签名和交易授权。
_IDENTITY_SUBSTR = ("owner_did", "requester_did", "caller_did", "provider_did",
                    "counterparty_did", "recipient_did", "relay_did", "buyer_did",
                    "asset_id", "lease_id", "session_id", "grn_id")
_DID_PATTERN = re.compile(r"did:a2n:[A-Za-z0-9._:%-]+")

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
    ("资产句柄", re.compile(r"\bas_[0-9a-f]{32}\b")),
)

_SAMPLE_TEXT_PATTERNS = (
    ("内嵌媒体", re.compile(r"data:[^\s\"']+;base64,[A-Za-z0-9+/=]+")),
    ("媒体字段", re.compile(
        r'''(?i)["'](?:file|files|path|url|uri|base64|bytes|assets)["']\s*:\s*["'][^"'\r\n]*["']''')),
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
            or text == "did" or text.endswith("_did")
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


def sample_projection(value: Any, removed: list) -> tuple[Any, bool]:
    """Project sample content at every nesting level, including legacy JSON text.

    Normal descriptions remain visible. Raw file fields stay in the private
    delivery; separately encoded thumbnails are carried outside this projection.
    """
    import json

    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (dict, list)):
                value = parsed
        except (ValueError, RecursionError):
            pass  # Old previews may be clipped JSON; text scrubbing still applies.
    media = False
    raw_fields = {"file", "files", "path", "url", "uri", "base64", "bytes", "assets",
                  "datauri", "contentbytes"}

    def project(item):
        nonlocal media
        if isinstance(item, dict):
            out = {}
            for key, child in item.items():
                name = str(key).lower().replace("_", "").replace("-", "")
                if name in raw_fields:
                    media = True
                    removed.append("媒体字段:" + str(key))
                    out[str(key)] = "[媒体内容保留在私有交付中]"
                elif is_secret(key):
                    removed.append(str(key))
                    out[str(key)] = _REDACTED
                else:
                    out[str(key)] = project(child)
            return out
        if isinstance(item, list):
            return [project(child) for child in item]
        if isinstance(item, str):
            item = scrub_text(item, removed)
            for label, pattern in _SAMPLE_TEXT_PATTERNS:
                if pattern.search(item):
                    media = True
                    item = pattern.sub("[媒体内容保留在私有交付中]", item)
                    removed.append(label)
        return item

    result = project(value)
    return result, media


def public_sample_media(value: Any) -> list[dict]:
    """Accept only bounded RGB PNG thumbnails without metadata or extra fields.

    This is a read boundary as well: old sample records must not bypass today's
    encoder constraints by carrying arbitrary base64 or private identifiers.
    """
    import base64
    import binascii
    import hashlib
    import struct
    import zlib

    if not isinstance(value, list):
        return []
    output = []
    for item in value[:2]:
        if not isinstance(item, dict) or item.get("mime_type") != "image/png":
            continue
        text = item.get("base64")
        if not isinstance(text, str) or len(text) > 10924:
            continue
        try:
            raw = base64.b64decode(text, validate=True)
            if not 45 <= len(raw) <= 8192 or raw[:8] != b"\x89PNG\r\n\x1a\n":
                continue
            position, kinds, payloads = 8, [], []
            while position < len(raw):
                length = struct.unpack(">I", raw[position:position + 4])[0]
                kind = raw[position + 4:position + 8]
                end = position + length + 12
                if end > len(raw) or kind not in {b"IHDR", b"IDAT", b"IEND"}:
                    raise ValueError("Not a metadata-free thumbnail")
                payload = raw[position + 8:position + length + 8]
                crc = struct.unpack(">I", raw[end - 4:end])[0]
                if zlib.crc32(kind + payload) != crc:
                    raise ValueError("Invalid PNG chunk")
                kinds.append(kind)
                payloads.append(payload)
                position = end
            if kinds[0] != b"IHDR" or kinds[-1] != b"IEND" or len(payloads[0]) != 13 or payloads[-1]:
                continue
            if kinds.count(b"IHDR") != 1 or kinds.count(b"IEND") != 1 or kinds[1:-1] != [b"IDAT"] * (len(kinds) - 2) or len(kinds) < 3:
                continue
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", payloads[0])
            if not 1 <= width <= 192 or not 1 <= height <= 192 or (depth, color, compression, filtering, interlace) != (8, 2, 0, 0, 0):
                continue
            digest = hashlib.sha256(raw).hexdigest()
            if (item.get("sha256") != digest or type(item.get("width")) is not int or type(item.get("height")) is not int
                    or item["width"] != width or item["height"] != height):
                continue
            output.append({"mime_type": "image/png", "base64": text, "sha256": digest, "width": width, "height": height})
        except (ValueError, binascii.Error, struct.error, IndexError):
            continue
    return output

