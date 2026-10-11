"""Align a NEW local Anvil epoch with retained wallet nonce reservations.

Explicit test-environment repair only. Never reset a node's reservation, replace
its wallet, fabricate a receipt, or advance past a queued original transaction.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from local_business_acceptance import api
from payment_testchain_check import rpc, request
from a2n_node.protection import system_protector
from a2n_sdk.storage import LocalStore
from a2n_sdk.trade_facts import digest
from eth_account import Account
from eth_utils import keccak

ROOT = Path(__file__).resolve().parents[1]


def reservation(node, wallet):
    key = digest(["eip155:31337", wallet])
    if node == "desktop":
        store = LocalStore(ROOT / "data/desktop-sdk/runtime.db", system_protector())
        try:
            return store.get("evm_next_nonce", key) or 0
        finally:
            store.close()
    code = ("import os,sys;from pathlib import Path;from a2n_sdk.storage import LocalStore;"
        "from a2n_node.protection import system_protector;"
        "s=LocalStore(Path(os.environ['A2N_HOME'])/'runtime.db',system_protector());"
        "print(s.get('evm_next_nonce',sys.argv[1]) or 0);s.close()")
    out = subprocess.run(["docker", "exec", "a2n-acceptance-" + node + "-1", "python", "-c", code, key],
                         capture_output=True, text=True, timeout=10)
    if out.returncode:
        raise RuntimeError("TEST_NONCE_RESERVATION_READ_FAILED")
    return int(out.stdout.strip())


def main():
    assert int(rpc("eth_chainId", []), 16) == 31337
    assert "anvil" in rpc("web3_clientVersion", []).lower()
    token = request("http://127.0.0.1:18946/status")["token"]
    genesis = rpc("eth_getBlockByNumber", ["0x0", False])["hash"]
    journal = ROOT / ".tmp/testchain-nonce-replay.bin"
    protector = system_protector()
    retained = json.loads(protector.open(journal.read_bytes())) if journal.exists() else {}
    saved = (retained.get("transactions", []) if retained.get("token") == token
             and retained.get("genesis") == genesis else [])
    rows = []
    for node in ("desktop", "node-a", "node-b", "node-c"):
        status = api(node, "/v1/payment-coordination")
        assert status.get("test_environment", {}).get("kind") == "LOCAL_ANVIL", "REAL_WALLETS_MUST_NOT_BE_REPAIRED"
        wallet = status["wallet_address"]
        reserved = reservation(node, wallet)
        assert type(reserved) is int and reserved >= 0
        current = int(rpc("eth_getTransactionCount", [wallet, "latest"]), 16)
        pool = rpc("txpool_content", [])
        transactions = [tx for category in ("pending", "queued")
                  for address, txs in pool.get(category, {}).items() if address.lower() == wallet.lower()
                  for tx in txs.values()]
        originals = [r for r in saved if r["wallet"].lower() == wallet.lower()
                     and not rpc("eth_getTransactionReceipt", [r["hash"]])]
        for tx in transactions:
            raw = rpc("eth_getRawTransactionByHash", [tx["hash"]])
            assert raw and "0x" + keccak(bytes.fromhex(raw[2:])).hex() == tx["hash"]
            assert Account.recover_transaction(raw).lower() == wallet.lower()
            original = {"wallet": wallet, "nonce": int(tx["nonce"], 16), "hash": tx["hash"], "raw": raw}
            if not any(r["hash"] == original["hash"] for r in originals):
                originals.append(original)
            if not any(r["hash"] == original["hash"] for r in saved):
                saved.append(original)
        # Keep the original signed bytes durably before temporarily removing a
        # stale queued pool entry. A stopped repair can replay this same hash.
        journal.parent.mkdir(exist_ok=True)
        journal.write_bytes(protector.seal(json.dumps({"token": token, "genesis": genesis, "transactions": saved}).encode()))
        queued = [r["nonce"] for r in originals]
        target = min(queued) if queued else max(current, reserved)
        if target > current:
            rpc("anvil_setNonce", [wallet, hex(target)])
        rpc("evm_mine", [])
        replayed = []
        for original in sorted(originals, key=lambda r: r["nonce"]):
            if rpc("eth_getTransactionReceipt", [original["hash"]]):
                continue
            # Anvil does not promote old queued entries after an explicit nonce
            # state edit. Reinsert exactly those bytes; never sign another order.
            rpc("anvil_dropTransaction", [original["hash"]])
            returned = rpc("eth_sendRawTransaction", [original["raw"]])
            assert returned == original["hash"]
            rpc("evm_mine", [])
            replayed.append(returned)
        rows.append({"node": node, "before_nonce": current, "epoch_nonce": max(current, target),
            "retained_next_reservation": reserved, "queued_original_nonces": sorted(queued),
            "wallet_replaced": False, "local_ledger_changed": False, "original_hashes_replayed": replayed})
    # Flush the paired test-chain checkpoint using a test-only fault reset.
    request("http://127.0.0.1:18946/faults/reset", {"fault": "rpc_response"})
    report = {"at": datetime.now(timezone.utc).isoformat(), "passed": True, "real_money": False,
              "kind": "EXPLICIT_LOCAL_TEST_EPOCH_REPAIR", "nodes": rows}
    (ROOT / "artifacts/payment-testchain-nonce-repair.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
