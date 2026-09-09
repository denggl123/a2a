"""内核：ID、哈希、时钟。所有随机性与时间都必须走这里，便于测试与审计。"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any

GENESIS = "0" * 64


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def chain_hash(prev_hash: str, fields: dict[str, Any]) -> str:
    """把一组字段与prev_hash串联成不可篡改的哈希。"""
    payload = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(f"{prev_hash}|{payload}")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
