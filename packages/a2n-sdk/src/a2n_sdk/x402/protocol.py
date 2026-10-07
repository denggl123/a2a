"""Bounded x402 v2 HTTP codec and exact/eip3009/authorization profile.

Wire field names are the Foundation's field names. Unsupported mechanisms are
rejected before signing; extensions are preserved without claiming to execute them.
"""
from __future__ import annotations

import base64
import binascii
import copy
import json
import re
from urllib.parse import urlsplit

PAYMENT_REQUIRED = "PAYMENT-REQUIRED"
PAYMENT_SIGNATURE = "PAYMENT-SIGNATURE"
PAYMENT_RESPONSE = "PAYMENT-RESPONSE"
MAX_HEADER = 8192
MAX_BODY = 1024 * 1024
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}\Z")
HASH = re.compile(r"0x[0-9a-fA-F]{64}\Z")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("X402_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def bounded_json(raw, *, limit=MAX_BODY):
    if not isinstance(raw, bytes) or len(raw) > limit:
        raise ValueError("X402_JSON_TOO_LARGE")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("X402_NONFINITE_JSON")))
        def walk(obj, depth=0):
            if depth > 16:
                raise ValueError("X402_JSON_TOO_DEEP")
            if isinstance(obj, dict):
                for v in obj.values():
                    walk(v, depth + 1)
            elif isinstance(obj, list):
                for v in obj:
                    walk(v, depth + 1)
        walk(value)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("X402_INVALID_JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("X402_EXPECTED_OBJECT")
    return value


def encode_header(value):
    if not isinstance(value, dict):
        raise ValueError("X402_EXPECTED_OBJECT")
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    bounded_json(raw, limit=MAX_HEADER)
    encoded = base64.b64encode(raw).decode("ascii")
    if len(encoded) > MAX_HEADER:
        raise ValueError("X402_HEADER_TOO_LARGE")
    return encoded


def decode_header(value):
    if not isinstance(value, str) or not value or len(value) > MAX_HEADER:
        raise ValueError("X402_INVALID_HEADER")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("X402_INVALID_BASE64") from exc
    return bounded_json(raw, limit=MAX_HEADER)


def header(headers, name):
    values = [value for key, value in headers.items() if key.lower() == name.lower()]
    if len(values) > 1:
        raise ValueError("X402_DUPLICATE_HEADER")
    return values[0] if values else None


def uint(value, *, positive=False):
    if (not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]{0,77}", value)
            or int(value) >= 2**256 or positive and int(value) == 0):
        raise ValueError("X402_INVALID_ATOMIC_AMOUNT")
    return int(value)


def address(value):
    if not isinstance(value, str) or not ADDRESS.fullmatch(value) or int(value[2:], 16) == 0:
        raise ValueError("X402_INVALID_ADDRESS")
    return value.lower()


def resource_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value):
        raise ValueError("X402_INVALID_RESOURCE_URL")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.fragment):
        raise ValueError("X402_INVALID_RESOURCE_URL")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("X402_INVALID_RESOURCE_URL") from exc
    return value


def validate_requirement(value):
    if not isinstance(value, dict):
        raise ValueError("X402_INVALID_REQUIREMENT")
    if value.get("scheme") != "exact":
        raise ValueError("X402_UNSUPPORTED_SCHEME")
    network = value.get("network")
    if not isinstance(network, str) or not re.fullmatch(r"eip155:[1-9][0-9]{0,19}", network):
        raise ValueError("X402_UNSUPPORTED_NETWORK")
    uint(value.get("amount"), positive=True)
    address(value.get("asset"))
    address(value.get("payTo"))
    timeout = value.get("maxTimeoutSeconds")
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise ValueError("X402_INVALID_TIMEOUT")
    extra = value.get("extra", {})
    if not isinstance(extra, dict):
        raise ValueError("X402_INVALID_EXTRA")
    if extra.get("assetTransferMethod", "eip3009") != "eip3009":
        raise ValueError("X402_UNSUPPORTED_TRANSFER_METHOD")
    if extra.get("paymentFlow", "authorization") != "authorization":
        raise ValueError("X402_UNSUPPORTED_PAYMENT_FLOW")
    if not all(isinstance(extra.get(k), str) and 1 <= len(extra[k]) <= 128 for k in ("name", "version")):
        raise ValueError("X402_MISSING_EIP712_DOMAIN")
    encode_header(value)
    return copy.deepcopy(value)


def validate_required(value):
    if not isinstance(value, dict) or type(value.get("x402Version")) is not int or value["x402Version"] != 2:
        raise ValueError("X402_UNSUPPORTED_VERSION")
    resource = value.get("resource")
    if not isinstance(resource, dict):
        raise ValueError("X402_INVALID_RESOURCE")
    resource_url(resource.get("url"))
    for name in ("description", "mimeType"):
        if name in resource and (not isinstance(resource[name], str) or len(resource[name]) > 2048):
            raise ValueError("X402_INVALID_RESOURCE_METADATA")
    def ascii_label(label):
        return isinstance(label, str) and 1 <= len(label) <= 32 and all(32 <= ord(c) <= 126 for c in label)
    if "serviceName" in resource and not ascii_label(resource["serviceName"]):
        raise ValueError("X402_INVALID_RESOURCE_METADATA")
    if "tags" in resource and (not isinstance(resource["tags"], list) or len(resource["tags"]) > 5
                               or not all(ascii_label(t) for t in resource["tags"])):
        raise ValueError("X402_INVALID_RESOURCE_METADATA")
    if "iconUrl" in resource:
        resource_url(resource["iconUrl"])
    accepts = value.get("accepts")
    if not isinstance(accepts, list) or not 1 <= len(accepts) <= 32 or not all(isinstance(r, dict) for r in accepts):
        raise ValueError("X402_INVALID_ACCEPTS")
    if not isinstance(value.get("extensions", {}), dict):
        raise ValueError("X402_INVALID_EXTENSIONS")
    encode_header(value)
    return copy.deepcopy(value)


def check_extensions(required, payload):
    """Required extension information must survive the client echo unchanged."""
    def subset(expected, actual):
        if isinstance(expected, dict):
            return isinstance(actual, dict) and all(k in actual and subset(v, actual[k]) for k, v in expected.items())
        return expected == actual
    if not subset(required.get("extensions", {}), payload.get("extensions", {})):
        raise ValueError("X402_EXTENSION_INFORMATION_CHANGED")


def payment_required(resource, accepts, *, error=None, extensions=None):
    value = {"x402Version": 2, "resource": copy.deepcopy(resource),
             "accepts": [validate_requirement(r) for r in accepts]}
    if error:
        value["error"] = str(error)[:200]
    if extensions:
        value["extensions"] = copy.deepcopy(extensions)
    return validate_required(value)


def validate_payload(value, requirement=None, *, now=None):
    if not isinstance(value, dict) or type(value.get("x402Version")) is not int or value["x402Version"] != 2:
        raise ValueError("X402_UNSUPPORTED_VERSION")
    accepted = validate_requirement(value.get("accepted"))
    if requirement is not None and accepted != validate_requirement(requirement):
        raise ValueError("X402_REQUIREMENT_MISMATCH")
    if "resource" in value:
        if not isinstance(value["resource"], dict):
            raise ValueError("X402_INVALID_RESOURCE")
        resource_url(value["resource"].get("url"))
    if not isinstance(value.get("extensions", {}), dict):
        raise ValueError("X402_INVALID_EXTENSIONS")
    payload = value.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("authorization"), dict):
        raise ValueError("X402_INVALID_AUTHORIZATION")
    auth = payload["authorization"]
    address(auth.get("from"))
    if address(auth.get("to")) != address(accepted["payTo"]) or uint(auth.get("value"), positive=True) != uint(accepted["amount"]):
        raise ValueError("X402_AUTHORIZATION_TERMS_MISMATCH")
    after, before = uint(auth.get("validAfter")), uint(auth.get("validBefore"))
    if after >= before or now is not None and not after < now < before:
        raise ValueError("X402_AUTHORIZATION_EXPIRED_OR_NOT_YET_VALID")
    nonce = auth.get("nonce")
    sig = payload.get("signature")
    if not isinstance(nonce, str) or not HASH.fullmatch(nonce):
        raise ValueError("X402_INVALID_NONCE")
    if not isinstance(sig, str) or not re.fullmatch(r"0x[0-9a-fA-F]{130}", sig):
        raise ValueError("X402_UNSUPPORTED_SIGNATURE")
    encode_header(value)
    return copy.deepcopy(value)


def validate_settlement(value, payload):
    if not isinstance(value, dict) or type(value.get("success")) is not bool:
        raise ValueError("X402_INVALID_SETTLEMENT")
    if value.get("network") != payload["accepted"]["network"]:
        raise ValueError("X402_SETTLEMENT_NETWORK_MISMATCH")
    if value.get("payer") is not None and address(value["payer"]) != address(payload["payload"]["authorization"]["from"]):
        raise ValueError("X402_SETTLEMENT_PAYER_MISMATCH")
    tx = value.get("transaction")
    if not isinstance(tx, str) or tx and not HASH.fullmatch(tx) or value["success"] and not tx:
        raise ValueError("X402_INVALID_TRANSACTION")
    return copy.deepcopy(value)
