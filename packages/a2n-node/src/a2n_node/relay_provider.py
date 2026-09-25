"""Provider-side outbound-only worker for a voluntary sealed relay."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

from a2n_sdk.upstream import _http_json

from .card import sign_card
from .relay_crypto import open_request, public_b64, seal_response
from .relay_service import sign_auth


def _aad(did: str, service_id: str, kind: str) -> bytes:
    return f"a2n-relay/1|{did}|{service_id}|{kind}".encode("utf-8")


def _post(url: str, body: dict, *, timeout: float = 8.0) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        value = json.loads(response.read(1_400_000))
    if not isinstance(value, dict):
        raise ValueError("公共中继返回结构无效")
    return value


class RelayProvider:
    def __init__(self, identity, runtime, private_key, relay_node: str):
        self.identity, self.runtime = identity, runtime
        self.private_key = private_key
        self.relay_node = relay_node.rstrip("/")
        self._stop = threading.Event()
        self._refresh = threading.Event()
        self._thread = None
        self.last_error = ""
        self.registered = 0

    def _cards(self) -> list[dict]:
        base = f"{self.relay_node}/relay/v1/{self.identity.did}"
        cards = []
        for binding in self.runtime.bindings.list():
            if not binding.enabled:
                continue
            card = self.runtime.project_binding(binding.service_id, public_base=base)
            card.setdefault("x-a2n", {})["relay"] = {
                "protocol": "a2n-sealed-relay/1", "node": self.relay_node,
                "public_key": public_b64(self.private_key)}
            cards.append(sign_card(self.identity, card))
        return cards

    def _authorized(self, action: str, body: dict, *, timeout=8) -> dict:
        return _post(f"{self.relay_node}/relay/v1/{action}", {
            **body, "auth": sign_auth(self.identity, action, body)}, timeout=timeout)

    def _register(self) -> None:
        cards = self._cards()
        result = self._authorized("register", {"cards": cards})
        self.registered = int(result.get("count") or 0)

    def _handle(self, job: dict) -> None:
        sid = str(job["service_id"])
        kind = str(job["kind"])
        aad = _aad(self.identity.did, sid, kind)
        value, key = open_request(self.private_key, job["envelope"], aad=aad)
        path = f"/a2a/{sid}" if kind == "a2a" else "/a2n/ack"
        try:
            status, body = _http_json(
                self.runtime.local_base_url + path, value, {}, 15, False)
        except Exception as exc:
            status, body = 502, {"error": f"本机供给转发失败：{type(exc).__name__}"}
        result = seal_response(key, {"status": status, "body": body}, aad=aad)
        self._authorized("complete", {
            "provider_did": self.identity.did, "job_id": job["job_id"],
            "result": result})

    def _run(self) -> None:
        next_register = 0.0
        while not self._stop.is_set():
            try:
                if self._refresh.is_set() or time.monotonic() >= next_register:
                    # Clear before I/O so a binding change during registration
                    # schedules another pass instead of being accidentally lost.
                    self._refresh.clear()
                    self._register()
                    next_register = time.monotonic() + 15
                result = self._authorized("poll", {
                    "provider_did": self.identity.did}, timeout=8)
                if result.get("job_id"):
                    self._handle(result)
                self.last_error = ""
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                self._stop.wait(1)

    def start(self):
        if not self._thread:
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="a2n-relay-provider")
            self._thread.start()
        return self

    def request_refresh(self):
        self._refresh.set()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=9)
