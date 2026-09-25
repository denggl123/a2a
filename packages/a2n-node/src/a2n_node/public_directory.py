"""Optional client for directories hosted by ordinary volunteer nodes.

Directory responses are untrusted hints: every returned Card is verified
locally and subsequent calls still go directly to the provider's signed URL.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import ipaddress
import json
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit

from .card import card_did, card_skills, verify_card

MAX_PUBLIC_NODES = 8
MAX_RESPONSE_BYTES = 2_100_000


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class PublicDirectoryClient:
    def __init__(self, bases, *, timeout: float = 3.0):
        normalized = []
        for raw in bases or ():
            base = str(raw or "").rstrip("/")
            parsed = urlsplit(base)
            try:
                parsed.port
            except ValueError as exc:
                raise ValueError(f"公共节点地址端口无效：{base}") from exc
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise ValueError(f"公共节点地址必须是纯 HTTP(S) 基础 URL：{base}")
            if parsed.scheme == "http":
                try:
                    local = ipaddress.ip_address(parsed.hostname).is_loopback
                except ValueError:
                    local = parsed.hostname == "localhost"
                if not local:
                    raise ValueError("非本机公共节点必须使用 HTTPS")
            if base not in normalized:
                normalized.append(base)
        if len(normalized) > MAX_PUBLIC_NODES:
            raise ValueError(f"最多配置 {MAX_PUBLIC_NODES} 个公共节点")
        self.bases = tuple(normalized)
        self.timeout = max(0.1, float(timeout))

    def search(self, skill: str, *, limit: int = 30) -> tuple[list[dict], list[dict]]:
        wanted = str(skill or "").strip()
        if not wanted or len(wanted) > 96:
            raise ValueError("能力标识必须为 1–96 个字符")
        count = max(1, min(int(limit), 50))
        if not self.bases:
            return [], []
        results, errors, by_base = [], [], {}
        with ThreadPoolExecutor(max_workers=min(4, len(self.bases)),
                                thread_name_prefix="a2n-public-search") as pool:
            futures = {pool.submit(self._fetch, base, wanted, count): base
                       for base in self.bases}
            for future in as_completed(futures):
                base = futures[future]
                try:
                    cards = future.result()
                except Exception as exc:
                    errors.append({"source": "public-node", "node": base,
                                   "error": f"{type(exc).__name__}: {exc}"})
                    continue
                by_base[base] = cards
        # User-configured order, not network response speed, determines display
        # order. No volunteer can buy priority through latency or response size.
        for base in self.bases:
            results.extend({"card": card, "source": "public-node", "headers": {}}
                           for card in by_base.get(base, ()))
        return results[:count], errors

    def _fetch(self, base: str, skill: str, limit: int) -> list[dict]:
        url = base + "/public/v1/agents?skill=" + quote(skill, safe="") + f"&limit={limit}"
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(request, timeout=self.timeout) as response:
                declared = response.getheader("Content-Length")
                if declared and int(declared) > MAX_RESPONSE_BYTES:
                    raise ValueError("公共目录响应过大")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise ValueError(f"公共节点返回 HTTP {exc.code}") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("公共目录响应过大")
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("cards"), list):
            raise ValueError("公共目录响应结构无效")
        valid = []
        for card in data["cards"][:limit]:
            if not isinstance(card, dict):
                continue
            ok, _why = verify_card(card, require_endpoint=True)
            projection = ((card.get("x-a2n") or {}).get("projection") or {})
            if (ok and skill in card_skills(card)
                    and projection.get("role") != "consumer"
                    and (not projection.get("node_did")
                         or projection["node_did"] == card_did(card))):
                valid.append(card)
        return valid
