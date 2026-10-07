"""Cross-check wire messages and signatures with an installed official x402 SDK.

Example: python scripts/check_x402_interop.py --reference-path .tmp/x402-reference
The reference SDK is a verification dependency only. No funded key or network
request is used by this check; production A2N does not import that SDK.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-path", required=True)
    parser.add_argument("--report")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.reference_path).resolve()))
    from x402.schemas import PaymentRequired, PaymentPayload, PaymentRequirements, SettleResponse
    from x402.http.utils import (decode_payment_required_header, encode_payment_required_header,
        decode_payment_signature_header, encode_payment_signature_header,
        decode_payment_response_header, encode_payment_response_header)
    from x402.mechanisms.evm.eip712 import hash_eip3009_authorization
    from x402.mechanisms.evm.types import ExactEIP3009Authorization
    from x402.mechanisms.evm.exact.client import ExactEvmScheme
    from eth_account import Account
    from a2n_sdk.x402 import payment_required, encode_header, decode_header, validate_payload
    from a2n_sdk.x402.protocol import validate_required, validate_settlement
    from a2n_node.x402.evm import EvmSigner, EvmVerifier

    req = {"scheme": "exact", "network": "eip155:84532", "amount": "10000",
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e", "payTo": "0x" + "22" * 20,
        "maxTimeoutSeconds": 60, "extra": {"name": "USDC", "version": "2"}}
    required = payment_required({"url": "https://interop.invalid/paid", "mimeType": "application/json"}, [req])
    ours = EvmSigner(bytes.fromhex("11" * 32)).sign(required, req)
    checks = []
    def check(name, condition):
        if not condition:
            raise RuntimeError(name)
        checks.append(name)
    check("a2n_required_to_official", decode_payment_required_header(encode_header(required)).model_dump(by_alias=True, exclude_none=True) == required)
    check("official_required_to_a2n", validate_required(decode_header(encode_payment_required_header(PaymentRequired.model_validate(required)))) == required)
    check("a2n_signature_header_to_official", decode_payment_signature_header(encode_header(ours)).model_dump(by_alias=True, exclude_none=True) == ours)
    check("official_signature_header_to_a2n", validate_payload(decode_header(encode_payment_signature_header(PaymentPayload.model_validate(ours)))) == ours)
    auth = ours["payload"]["authorization"]
    official_hash = hash_eip3009_authorization(ExactEIP3009Authorization(
        auth["from"], auth["to"], auth["value"], auth["validAfter"], auth["validBefore"], auth["nonce"]),
        84532, req["asset"], "USDC", "2")
    check("a2n_signature_official_eip3009_hash", Account._recover_hash(official_hash, signature=ours["payload"]["signature"]).lower() == auth["from"].lower())
    official_inner = ExactEvmScheme(Account.from_key(bytes.fromhex("12" * 32))).create_payment_payload(PaymentRequirements.model_validate(req))
    official_payload = {"x402Version": 2, "resource": required["resource"], "accepted": req, "payload": official_inner}
    check("official_client_signature_verified_by_a2n", EvmVerifier().verify_signature(official_payload))
    receipt = {"success": True, "network": req["network"], "transaction": "0x" + "ab" * 32, "payer": auth["from"]}
    check("a2n_settlement_to_official", decode_payment_response_header(encode_header(receipt)).model_dump(by_alias=True, exclude_none=True) == receipt)
    check("official_settlement_to_a2n", validate_settlement(decode_header(encode_payment_response_header(SettleResponse.model_validate(receipt))), ours) == receipt)
    report = {"ok": True, "reference": "x402 Python SDK 2.25.0", "protocol": "x402 v2 exact EVM EIP-3009",
              "checks": checks, "checks_passed": len(checks), "real_funds_used": False, "chain_transactions_sent": 0}
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
