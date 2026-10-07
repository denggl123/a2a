"""EIP-712/EIP-3009 EOA signing and receipt verification on a pinned EVM RPC.

Only signing uses a wallet key. Ledger observations are read-only and verify
chain, token, nonce, payer, payee, amount, block identity and confirmation depth.
"""
from __future__ import annotations

import json
from pathlib import Path
import secrets
import time

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_abi import encode
from eth_keys.exceptions import BadSignature
from eth_utils import keccak

from a2n_sdk.x402.protocol import HASH, address, validate_payload, validate_requirement
from .http import HTTPClient

TRANSFER_TOPIC = "0x" + keccak(text="Transfer(address,address,uint256)").hex()
USED_TOPIC = "0x" + keccak(text="AuthorizationUsed(address,bytes32)").hex()


def typed_authorization(requirement, authorization):
    req = validate_requirement(requirement)
    return {"types": {
        "EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}],
        "TransferWithAuthorization": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"}, {"name": "validAfter", "type": "uint256"},
            {"name": "validBefore", "type": "uint256"}, {"name": "nonce", "type": "bytes32"}]},
        "primaryType": "TransferWithAuthorization",
        "domain": {"name": req["extra"]["name"], "version": req["extra"]["version"],
            "chainId": int(req["network"].split(":")[1]), "verifyingContract": req["asset"]},
        "message": {**authorization, "value": int(authorization["value"]),
            "validAfter": int(authorization["validAfter"]), "validBefore": int(authorization["validBefore"])}}


class EvmSigner:
    def __init__(self, private_key):
        self._account = Account.from_key(private_key)
        self.address = self._account.address

    @classmethod
    def from_keystore(cls, path, password):
        path = Path(path)
        if path.stat().st_size > 65536:
            raise ValueError("X402_KEYSTORE_TOO_LARGE")
        return cls(Account.decrypt(json.loads(path.read_text(encoding="utf-8")), password))

    def sign(self, required, requirement, *, now=None):
        requirement = validate_requirement(requirement)
        current = int(time.time() if now is None else now)
        auth = {"from": self.address, "to": requirement["payTo"], "value": requirement["amount"],
            "validAfter": "0", "validBefore": str(current + requirement["maxTimeoutSeconds"]),
            "nonce": "0x" + secrets.token_bytes(32).hex()}
        message = encode_typed_data(full_message=typed_authorization(requirement, auth))
        signature = "0x" + self._account.sign_message(message).signature.hex()
        payload = {"x402Version": 2, "resource": required["resource"], "accepted": requirement,
                   "payload": {"signature": signature, "authorization": auth}}
        if required.get("extensions"):
            payload["extensions"] = required["extensions"]
        return validate_payload(payload, requirement, now=current)


class EvmVerifier:
    def verify_signature(self, payload):
        try:
            payload = validate_payload(payload)
            auth = payload["payload"]["authorization"]
            recovered = Account.recover_message(encode_typed_data(full_message=typed_authorization(payload["accepted"], auth)),
                signature=payload["payload"]["signature"])
            return address(recovered) == address(auth["from"])
        except (ValueError, TypeError, BadSignature):
            return False


def self_test():
    """Exercise packaged secp256k1, ABI and keccak without contacting a network."""
    from a2n_sdk.x402 import payment_required
    req = {"scheme": "exact", "network": "eip155:84532", "amount": "1",
        "asset": "0x" + "33" * 20, "payTo": "0x" + "22" * 20, "maxTimeoutSeconds": 60,
        "extra": {"name": "USDC", "version": "2"}}
    payload = EvmSigner(bytes.fromhex("11" * 32)).sign(payment_required({"url": "https://selftest.invalid/"}, [req]), req)
    if not EvmVerifier().verify_signature(payload) or len(encode(["uint256"], [1])) != 32:
        raise RuntimeError("x402 native signature verification failed")
    return True


class EvmRPC:
    def __init__(self, url, http=None):
        self.url, self.http = url, http or HTTPClient()
        self.http.validate_url(url)

    def call(self, method, params):
        result = self.http.json("POST", self.url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if result.get("jsonrpc") != "2.0" or result.get("id") != 1 or "error" in result or "result" not in result:
            raise ValueError("X402_RPC_ERROR")
        return result["result"]


def topic_address(value):
    return "0x" + address(value)[2:].rjust(64, "0")


class EvmObserver:
    def __init__(self, rpc, *, network, asset, name, version, confirmations=3, scan_limit=10000):
        if type(confirmations) is not int or not 1 <= confirmations <= 1000 or type(scan_limit) is not int or not 1 <= scan_limit <= 10000:
            raise ValueError("X402_INVALID_FINALITY_CONFIG")
        self.rpc, self.network, self.asset = rpc, network, address(asset)
        self.name, self.version = name, version
        self.confirmations, self.scan_limit = confirmations, scan_limit

    def _check(self, payload):
        req = validate_payload(payload)["accepted"]
        if (req["network"] != self.network or address(req["asset"]) != self.asset
                or req["extra"]["name"] != self.name or req["extra"]["version"] != self.version):
            raise ValueError("X402_UNTRUSTED_ASSET_OR_DOMAIN")
        if int(self.rpc.call("eth_chainId", []), 16) != int(self.network.split(":")[1]):
            raise ValueError("X402_RPC_WRONG_CHAIN")

    def anchor(self, payload):
        self._check(payload)
        return int(self.rpc.call("eth_blockNumber", []), 16)

    def _receipt(self, payload, tx, latest):
        if not isinstance(tx, str) or not HASH.fullmatch(tx):
            return None
        receipt = self.rpc.call("eth_getTransactionReceipt", [tx])
        if not isinstance(receipt, dict) or receipt.get("transactionHash", "").lower() != tx.lower():
            return None
        if int(receipt.get("status", "0x0"), 16) != 1:
            return None
        block = int(receipt["blockNumber"], 16)
        if latest - block + 1 < self.confirmations:
            return None
        canonical = self.rpc.call("eth_getBlockByNumber", [hex(block), False])
        if not canonical or canonical["hash"].lower() != receipt["blockHash"].lower():
            return None
        auth = payload["payload"]["authorization"]
        transfer, used = False, False
        for log in receipt.get("logs", []):
            if log.get("removed") or address(log.get("address")) != self.asset:
                continue
            if (log.get("transactionHash", "").lower() != tx.lower()
                    or log.get("blockHash", "").lower() != receipt["blockHash"].lower()):
                continue
            topics = [t.lower() for t in log.get("topics", [])]
            if topics == [TRANSFER_TOPIC, topic_address(auth["from"]), topic_address(auth["to"])]:
                transfer |= int(log.get("data", "0x0"), 16) == int(auth["value"])
            if topics == [USED_TOPIC, topic_address(auth["from"]), auth["nonce"].lower()]:
                used = True
        if not transfer or not used:
            return None
        return {"success": True, "network": self.network, "payer": auth["from"], "transaction": tx}

    def observe(self, payload, anchor, hint=None):
        try:
            self._check(payload)
            if type(anchor) is not int or anchor < 0:
                raise ValueError("X402_INVALID_ANCHOR")
            latest = int(self.rpc.call("eth_blockNumber", []), 16)
            tx = (hint or {}).get("transaction")
            if tx:
                receipt = self._receipt(payload, tx, latest)
                if receipt:
                    return {"state": "CONFIRMED", "settlement": receipt}
            finalized = latest - self.confirmations + 1
            if finalized < anchor or finalized - anchor > self.scan_limit:
                return {"state": "UNKNOWN", "reason": "LEDGER_SCAN_NOT_COMPLETE"}
            auth = payload["payload"]["authorization"]
            # Scan only the configured token and exact authorization. JSON-RPC
            # has no x402 /query equivalent; this is explicitly a ledger adapter.
            for start in range(anchor, finalized + 1, 2000):
                logs = self.rpc.call("eth_getLogs", [{"address": self.asset, "fromBlock": hex(start),
                    "toBlock": hex(min(start + 1999, finalized)),
                    "topics": [USED_TOPIC, topic_address(auth["from"]), auth["nonce"]]}])
                if not isinstance(logs, list) or len(logs) > 100:
                    raise ValueError("X402_INVALID_LEDGER_LOGS")
                for log in logs:
                    receipt = self._receipt(payload, log.get("transactionHash"), latest)
                    if receipt:
                        return {"state": "CONFIRMED", "settlement": receipt}
            block = self.rpc.call("eth_getBlockByNumber", [hex(finalized), False])
            if block and int(block["timestamp"], 16) > int(auth["validBefore"]):
                data = keccak(text="authorizationState(address,bytes32)")[:4] + encode(
                    ["address", "bytes32"], [auth["from"], bytes.fromhex(auth["nonce"][2:])])
                value = self.rpc.call("eth_call", [{"to": self.asset, "data": "0x" + data.hex()}, hex(finalized)])
                if isinstance(value, str) and value.lower() == "0x" + "0" * 64:
                    return {"state": "FAILED", "definitive": True, "reason": "EXPIRED_UNUSED_AUTHORIZATION"}
            return {"state": "UNKNOWN", "reason": "LEDGER_RESULT_NOT_FINAL"}
        except Exception:
            return {"state": "UNKNOWN", "reason": "LEDGER_QUERY_UNAVAILABLE"}
