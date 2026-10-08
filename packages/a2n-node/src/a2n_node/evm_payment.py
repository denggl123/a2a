"""Noncustodial EIP-1559 native transfers, durably signed before one broadcast."""
from __future__ import annotations

import threading
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak
from a2n_sdk.payments import minor
from a2n_sdk.trade_facts import digest
from a2n_sdk.x402.protocol import address
from .x402.evm import EvmRPC
from .x402.http import HTTPClient

METHOD = "evm-native/1"


def wallet_binding(signer, did):
    message = encode_defunct(text="a2n-wallet-binding/1:" + did)
    return {"address": signer.address, "signature": "0x" + signer._account.sign_message(message).signature.hex()}


def verify_wallet(binding, did):
    try:
        recovered = Account.recover_message(encode_defunct(text="a2n-wallet-binding/1:" + did), signature=binding["signature"])
        if address(recovered) != address(binding["address"]):
            raise ValueError("WALLET_BINDING_MISMATCH")
    except Exception as exc:
        raise ValueError("INVALID_WALLET_BINDING") from exc


class EvmNativeDriver:
    def __init__(self, store, config, signer=None, *, rpc=None):
        if not isinstance(config, dict) or set(config) - {"currency", "network", "rpc_url", "confirmations", "allow_http"}:
            raise ValueError("INVALID_EVM_PAYMENT_CONFIG")
        import re
        if not re.fullmatch(r"[A-Z]{2,12}", str(config.get("currency", ""))) or not re.fullmatch(r"eip155:[1-9][0-9]{0,19}", str(config.get("network", ""))):
            raise ValueError("INVALID_EVM_PAYMENT_ASSET")
        confirmations = config.get("confirmations", 3)
        if isinstance(confirmations, bool) or not isinstance(confirmations, int) or not 1 <= confirmations <= 64:
            raise ValueError("INVALID_EVM_CONFIRMATIONS")
        self.store, self.config, self.signer = store, dict(config), signer
        http = HTTPClient(allow_http=config.get("allow_http", False))
        http.validate_url(config.get("rpc_url"))
        self.rpc = rpc or EvmRPC(config["rpc_url"], http)
        self.driver_id = METHOD + ":" + digest([config, signer.address if signer else None])
        self._lock = threading.RLock()
        for key, row in store.items("evm_transfers").items():
            if row.get("driver_id") == self.driver_id and row["state"] == "PREPARING":
                store.put("evm_transfers", key, {**row, "state": "NOT_EXPOSED"})

    def capabilities(self):
        return {"driver_id": self.driver_id, "currencies": [self.config["currency"]] if self.signer else [],
            "method": METHOD, "network": self.config["network"], "asset": "native", "flow": "upfront",
            "refund": True, "platform_commission_minor": 0}

    def descriptor(self):
        return {"method": METHOD, "currency": self.config["currency"], "network": self.config["network"],
            "asset": "native", "flow": "upfront"}

    def _chain(self):
        if int(self.rpc.call("eth_chainId", []), 16) != int(self.config["network"].split(":")[1]):
            raise ValueError("EVM_CHAIN_MISMATCH")

    def _result(self, intent, state, reason, **extra):
        return {"driver_id": self.driver_id, "verified_source": True, "state": state, "reason": reason,
            "currency": intent["currency"], "amount_minor": intent["amount_minor"], "payee": intent["payee"], **extra}

    def submit(self, intent):
        key = intent["intent_id"]
        with self._lock:
            with self.store.tx():
                old = self.store.get("evm_transfers", key)
                if not old:
                    self.store.put("evm_transfers", key, {"state": "PREPARING", "driver_id": self.driver_id})
            if old:
                return self.query(intent)
            try:
                self._chain()
                if not self.signer or intent["currency"] != self.config["currency"] or intent["driver_id"] != self.driver_id:
                    raise ValueError("EVM_INTENT_MISMATCH")
                payee = intent["payee"]
                address(payee)
                if self.rpc.call("eth_getCode", [payee, "latest"]) not in {"0x", "0x0"}:
                    raise ValueError("EVM_NATIVE_RECIPIENT_MUST_BE_EOA")
                from eth_utils import to_checksum_address
                tag = digest(["a2n-evm-payment/1", intent.get("plan_id") or key])
                data = "0x" + tag
                gas = 21000 + sum(4 if v == 0 else 16 for v in bytes.fromhex(tag))
                gas = max(gas, int(self.rpc.call("eth_estimateGas", [{"from": self.signer.address,
                    "to": payee, "value": hex(intent["amount_minor"]), "data": data}]), 16))
                if gas > 200000:
                    raise ValueError("EVM_GAS_LIMIT_EXCEEDED")
                fee_cap = minor(intent.get("fee_cap_minor", 0))
                maximum = fee_cap // gas
                block = self.rpc.call("eth_getBlockByNumber", ["latest", False])
                base = int(block["baseFeePerGas"], 16)
                priority = min(10**9, max(0, maximum - 2 * base))
                if maximum < 2 * base + priority or not maximum:
                    raise ValueError("EVM_FEE_CAP_TOO_LOW")
                nonce_key = digest([self.config["network"], self.signer.address])
                pending_nonce = int(self.rpc.call("eth_getTransactionCount", [self.signer.address, "pending"]), 16)
                # This commit is the point after which the funds might move.
                # Different saved RPC configurations for one wallet share this
                # atomic nonce reservation, including concurrent old/new orders.
                with self.store.tx():
                    nonce = max(pending_nonce, self.store.get("evm_next_nonce", nonce_key) or 0)
                    tx = {"type": 2, "chainId": int(self.config["network"].split(":")[1]), "nonce": nonce,
                        "to": to_checksum_address(payee), "value": intent["amount_minor"], "gas": gas,
                        "maxFeePerGas": maximum, "maxPriorityFeePerGas": priority, "data": data}
                    signed = self.signer._account.sign_transaction(tx)
                    raw = "0x" + signed.raw_transaction.hex()
                    tx_hash = "0x" + keccak(signed.raw_transaction).hex()
                    self.store.put("evm_transfers", key, {"state": "EXPOSED", "driver_id": self.driver_id,
                        "raw": raw, "hash": tx_hash, "payer": self.signer.address, "nonce": nonce,
                        "tag": data, "fee_cap_minor": fee_cap})
                    self.store.put("evm_next_nonce", nonce_key, nonce + 1)
            except Exception as exc:
                self.store.put("evm_transfers", key, {"state": "NOT_EXPOSED", "driver_id": self.driver_id})
                return self._result(intent, "FAILED", str(exc)[:200], definitive=True)
            try:
                returned = self.rpc.call("eth_sendRawTransaction", [raw])
                if returned.lower() != tx_hash.lower():
                    raise ValueError("EVM_BROADCAST_HASH_MISMATCH")
            except Exception:
                pass  # RPC rejection is insufficient proof that the signed tx cannot spend.
        return self.query(intent)

    def observe(self, *, reference, payer, payee, amount_minor, plan_id, fee_cap_minor=None):
        import re
        if not isinstance(reference, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", reference):
            raise ValueError("INVALID_EVM_REFERENCE")
        self._chain()
        receipt = self.rpc.call("eth_getTransactionReceipt", [reference])
        if not receipt:
            return {"state": "UNKNOWN", "reason": "EVM_RECEIPT_PENDING", "reference": reference}
        latest = int(self.rpc.call("eth_blockNumber", []), 16)
        height = int(receipt["blockNumber"], 16)
        block = self.rpc.call("eth_getBlockByNumber", [hex(height), False])
        if (latest - height + 1 < self.config.get("confirmations", 3) or not block
                or block["hash"].lower() != receipt["blockHash"].lower()
                or receipt["transactionHash"].lower() != reference.lower()):
            return {"state": "UNKNOWN", "reason": "EVM_CONFIRMATIONS_OR_REORG", "reference": reference}
        tx = self.rpc.call("eth_getTransactionByHash", [reference])
        tag = "0x" + digest(["a2n-evm-payment/1", plan_id])
        if (not tx or tx["hash"].lower() != reference.lower() or address(tx["from"]) != address(payer)
                or address(tx["to"]) != address(payee) or int(tx["value"], 16) != amount_minor
                or tx.get("input", tx.get("data", "")).lower() != tag
                or int(tx["chainId"], 16) != int(self.config["network"].split(":")[1])):
            return {"state": "UNKNOWN", "reason": "EVM_TRANSFER_TERMS_MISMATCH", "reference": reference}
        fee = int(receipt["gasUsed"], 16) * int(receipt["effectiveGasPrice"], 16)
        if fee_cap_minor is not None and fee > fee_cap_minor:
            return {"state": "UNKNOWN", "reason": "EVM_FEE_CAP_MISMATCH", "reference": reference}
        success = int(receipt["status"], 16) == 1
        return {"state": "CONFIRMED" if success else "FAILED", "definitive": not success,
            "reference": reference, "fee_minor": fee, "reason": "CANONICAL_EVM_RECEIPT",
            "ledger": {"receipt": receipt, "transaction": tx, "confirmed_tip": latest}}

    def query(self, intent):
        row = self.store.get("evm_transfers", intent["intent_id"])
        if row and row["state"] == "NOT_EXPOSED":
            return self._result(intent, "FAILED", "TRANSACTION_NOT_EXPOSED", definitive=True)
        if not row or row["state"] != "EXPOSED":
            return self._result(intent, "UNKNOWN", "EVM_TRANSFER_UNKNOWN")
        try:
            result = self.observe(reference=row["hash"], payer=row["payer"], payee=intent["payee"],
                amount_minor=intent["amount_minor"], plan_id=intent.get("plan_id") or intent["intent_id"],
                fee_cap_minor=row["fee_cap_minor"])
            return self._result(intent, result.pop("state"), result.pop("reason"), **result)
        except Exception:
            return self._result(intent, "UNKNOWN", "EVM_QUERY_UNAVAILABLE", reference=row["hash"])

    def refund(self, intent):
        raise ValueError("USE_SEPARATE_EXPLICIT_REFUND_ORDER")
