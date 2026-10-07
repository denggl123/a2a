"""Bounded direct HTTP; payment authorizations never follow redirects."""
from __future__ import annotations

from dataclasses import dataclass
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from a2n_sdk.x402.protocol import MAX_BODY, bounded_json, resource_url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass
class HTTPResponse:
    status: int
    headers: dict
    body: bytes


class HTTPClient:
    def __init__(self, *, timeout=15, allow_http=False):
        if type(timeout) not in {int, float} or not 0 < timeout <= 30 or type(allow_http) is not bool:
            raise ValueError("X402_INVALID_HTTP_CONFIG")
        self.timeout, self.allow_http = timeout, allow_http
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def validate_url(self, url):
        resource_url(url)
        if urlsplit(url).scheme != "https" and not self.allow_http:
            raise ValueError("X402_HTTPS_REQUIRED")

    def request(self, method, url, *, body=None, headers=None):
        self.validate_url(url)
        if body is not None and len(body) > MAX_BODY:
            raise ValueError("X402_REQUEST_TOO_LARGE")
        req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        try:
            response = self.opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            # Keep repeated headers detectable instead of silently choosing one.
            result_headers = {}
            for k, v in response.headers.items():
                if k.lower() in {n.lower() for n in result_headers}:
                    if k.lower().startswith("payment-"):
                        raise ValueError("X402_DUPLICATE_HEADER")
                    continue
                result_headers[k] = v
            raw = response.read(MAX_BODY + 1)
            if len(raw) > MAX_BODY:
                raise ValueError("X402_RESPONSE_TOO_LARGE")
            return HTTPResponse(response.status, result_headers, raw)

    def json(self, method, url, body=None):
        data = json.dumps(body, allow_nan=False, separators=(",", ":")).encode() if body is not None else None
        result = self.request(method, url, body=data, headers={"Content-Type": "application/json"})
        if not 200 <= result.status < 300:
            raise ValueError("X402_HTTP_SERVICE_ERROR")
        return bounded_json(result.body)


class HTTPFacilitator:
    def __init__(self, base_url, http=None):
        self.http = http or HTTPClient()
        self.http.validate_url(base_url)
        parsed = urlsplit(base_url)
        if parsed.query:
            raise ValueError("X402_INVALID_FACILITATOR_URL")
        self.base_url = base_url.rstrip("/")

    def supported(self):
        result = self.http.json("GET", self.base_url + "/supported")
        if (not isinstance(result.get("kinds"), list) or not isinstance(result.get("extensions"), list)
                or not isinstance(result.get("signers"), dict)):
            raise ValueError("X402_INVALID_SUPPORTED_RESPONSE")
        return result

    def _request(self, operation, payload, requirements):
        return self.http.json("POST", self.base_url + "/" + operation,
            {"x402Version": 2, "paymentPayload": payload, "paymentRequirements": requirements})

    def verify(self, payload, requirements):
        return self._request("verify", payload, requirements)

    def settle(self, payload, requirements):
        return self._request("settle", payload, requirements)
