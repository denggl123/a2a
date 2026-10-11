"""Actual local Anvil transfer and optional paired-container restart verification."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
import urllib.request

from eth_account import Account
from eth_abi import encode, decode
from eth_utils import keccak
from a2n_node.x402.evm import EvmSigner
from a2n_sdk.x402 import payment_required

ROOT = Path(__file__).resolve().parents[1]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(url, body=None):
    req = urllib.request.Request(url, None if body is None else json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with OPENER.open(req, timeout=10) as response:
        return json.load(response)


def rpc(method, params):
    row = request("http://127.0.0.1:18945", {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if "error" in row:
        raise ValueError("TEST_RPC_FAILED")
    return row["result"]


def balance(token, address):
    data = "0x" + (keccak(text="balanceOf(address)")[:4] + encode(["address"], [address])).hex()
    return decode(["uint256"], bytes.fromhex(rpc("eth_call", [{"to": token, "data": data}, "latest"])[2:]))[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args(argv)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            request("http://127.0.0.1:18946/status")
            break
        except (OSError, ValueError):
            time.sleep(.2)
    else:
        raise TimeoutError("TEST_ENVIRONMENT_STARTUP_FAILED")
    assert int(rpc("eth_chainId", []), 16) == 31337
    assert "anvil" in rpc("web3_clientVersion", []).lower()
    endpoint = "http://127.0.0.1:18946"
    status = request(endpoint + "/status")
    token = status["token"]
    signer = EvmSigner(Account.create().key)
    payee = Account.create().address
    request(endpoint + "/mint", {"address": signer.address})
    req = {"scheme": "exact", "network": "eip155:31337", "asset": token,
           "amount": "123", "payTo": payee, "maxTimeoutSeconds": 300,
           "extra": {"name": "A2N Test Token", "version": "1"}}
    required = payment_required({"url": "http://127.0.0.1:18946/local-restart-check"}, [req])
    now = int(rpc("eth_getBlockByNumber", ["latest", False])["timestamp"], 16)
    payload = signer.sign(required, req, now=now)
    body = {"paymentPayload": payload, "paymentRequirements": req}
    assert request(endpoint + "/verify", body)["isValid"]
    first = request(endpoint + "/settle", body)
    assert first["success"] and balance(token, payee) == 123
    original_receipt = rpc("eth_getTransactionReceipt", [first["transaction"]])
    assert int(original_receipt["status"], 16) == 1
    if args.restart:
        subprocess.run(["docker", "compose", "-f", "docker/testchain.yaml", "restart"],
                       cwd=ROOT, check=True, capture_output=True, timeout=40)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if request(endpoint + "/status")["token"] == token:
                    break
            except (OSError, ValueError):
                pass
            time.sleep(.2)
        else:
            raise TimeoutError("TEST_ENVIRONMENT_RESTART_FAILED")
    replay = request(endpoint + "/settle", body)
    assert replay == first and balance(token, payee) == 123 and balance(token, signer.address) == 999877
    recovered_receipt = rpc("eth_getTransactionReceipt", [first["transaction"]])
    assert recovered_receipt == original_receipt
    report = {"at": datetime.now(timezone.utc).isoformat(), "passed": True,
        "kind": "LOCAL_ANVIL_REAL_TRANSFER", "restarted": args.restart,
        "token": token, "transaction": first["transaction"], "amount_minor": 123,
        "receipt_preserved": True, "same_authorization_replayed_once": True,
        "legacy_volumes_preserved": True, "real_money": False}
    (ROOT / "artifacts/payment-testchain-restart.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
