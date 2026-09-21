"""Short-lived pairing codes and origin-bound local management sessions."""
from __future__ import annotations

import hmac
import ipaddress
import secrets
import threading
import time
from urllib.parse import urlsplit


def loopback_url(value: str) -> bool:
    try:
        u = urlsplit(value)
        if u.scheme not in {"http", "https"} or u.username or u.password:
            return False
        return u.hostname == "localhost" or ipaddress.ip_address(u.hostname or "").is_loopback
    except ValueError:
        return False


class PairingService:
    def __init__(self, *, origins: list[str] | None = None, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.RLock()
        self.origins = set(origins or [])
        self._sessions = {}
        self._failures = 0
        self._code = ""
        self._expires = 0.0

    def origin_allowed(self, origin: str) -> bool:
        return not origin or loopback_url(origin) or origin in self.origins

    def new_code(self) -> str:
        with self._lock:
            self._code = f"{secrets.randbelow(100000000):08d}"
            self._expires = self._clock() + 300
            self._failures = 0
            return self._code

    def exchange(self, code: str, origin: str) -> dict:
        with self._lock:
            if not self.origin_allowed(origin):
                raise PermissionError("此控制台来源未获允许")
            if self._clock() >= self._expires or self._failures >= 5 or not self._code:
                raise PermissionError("配对码已失效，请在节点终端生成新码")
            if not hmac.compare_digest(str(code), self._code):
                self._failures += 1
                raise PermissionError("配对码不正确")
            self._code = ""
            self._sessions = {k: v for k, v in self._sessions.items() if v[1] > self._clock()}
            if len(self._sessions) >= 32:
                self._sessions.pop(next(iter(self._sessions)))
            token = secrets.token_urlsafe(32)
            self._sessions[token] = (origin, self._clock() + 8 * 3600)
            return {"token": token, "expires_in": 8 * 3600}

    def verify(self, token: str, origin: str) -> bool:
        with self._lock:
            item = self._sessions.get(token)
            return bool(item and item[0] == origin and item[1] > self._clock())

    def revoke(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)
