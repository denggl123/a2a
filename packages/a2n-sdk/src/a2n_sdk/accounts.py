"""节点本地账户保险箱的最小接口。

SDK 只负责把账户引用解析成调用凭据；它不认识任何支付或模型品牌。默认实现仅驻留
内存，避免为了“方便”把 token 明文写盘。生产可以换成系统钥匙串实现，只需保留
``headers(account_id)`` 这一个边界。
"""
from __future__ import annotations

import copy
import threading
from typing import Any


_SECRET_WORDS = ("token", "secret", "password", "authorization", "api-key", "apikey")


def _redact(value: Any, key: str = "") -> Any:
    if any(word in key.lower() for word in _SECRET_WORDS):
        return "***"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return copy.deepcopy(value)


class MemoryAccountVault:
    """进程内账户库；列表视图永远脱敏。"""

    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def put(self, account_id: str, *, kind: str = "agent",
            label: str = "", headers: dict[str, str] | None = None,
            metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        if not account_id or not str(account_id).strip():
            raise ValueError("account_id 不能为空")
        item = {
            "account_id": str(account_id),
            "kind": str(kind or "agent"),
            "label": str(label or account_id),
            "headers": {str(k): str(v) for k, v in (headers or {}).items()},
            "metadata": copy.deepcopy(metadata or {}),
        }
        with self._lock:
            self._items[item["account_id"]] = item
        return self.public(item["account_id"])

    def remove(self, account_id: str) -> bool:
        with self._lock:
            return self._items.pop(account_id, None) is not None

    def headers(self, account_id: str | None) -> dict[str, str]:
        if not account_id:
            return {}
        with self._lock:
            item = self._items.get(account_id)
            if not item:
                raise KeyError(f"账户不存在：{account_id}")
            return dict(item["headers"])

    def public(self, account_id: str) -> dict[str, Any]:
        with self._lock:
            item = self._items.get(account_id)
            if not item:
                raise KeyError(f"账户不存在：{account_id}")
            names = []
            for key in item["headers"]:
                low = key.lower()
                names.append({"name": key,
                              "sensitive": any(word in low for word in _SECRET_WORDS)})
            return {
                "account_id": item["account_id"],
                "kind": item["kind"],
                "label": item["label"],
                "headers": names,
                "metadata": _redact(item["metadata"]),
            }

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            ids = list(self._items)
        return [self.public(account_id) for account_id in ids]
