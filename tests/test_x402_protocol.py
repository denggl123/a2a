"""Wire conformance and authorization tamper tests using an unfunded EOA."""
import base64
import copy

import pytest
from eth_abi import encode
from eth_account import Account
from eth_utils import keccak

from a2n_node.x402.evm import EvmSigner, EvmVerifier
from a2n_sdk.x402 import encode_header, decode_header, payment_required, validate_payload, validate_requirement
from a2n_sdk.x402.protocol import validate_required, validate_settlement


def requirement():
    return {"scheme": "exact", "network": "eip155:84532", "amount": "10000",
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e", "payTo": "0x" + "22" * 20,
        "maxTimeoutSeconds": 60, "extra": {"name": "USDC", "version": "2"}}


def required(url="https://seller.test/paid"):
    return payment_required({"url": url, "description": "Agent computation", "mimeType": "application/json"}, [requirement()])


def signed(now=1000):
    return EvmSigner(bytes.fromhex("11" * 32)).sign(required(), requirement(), now=now)


def test_standard_wire_roundtrip_preserves_extensions_and_optional_fields():
    value = required()
    value["extensions"] = {"test": {"info": {"message": "中文"}, "schema": {"type": "object"}}}
    assert decode_header(encode_header(value)) == value
    payload = EvmSigner(bytes.fromhex("11" * 32)).sign(value, requirement(), now=1000)
    assert payload["extensions"] == value["extensions"]
    assert validate_payload(decode_header(encode_header(payload)), requirement(), now=1001) == payload
    # Resource is optional on the standard PaymentPayload.
    payload.pop("resource")
    assert validate_payload(payload)


@pytest.mark.parametrize("encoded", ["", "!", "e30=\n", "e30=" * 3000,
    base64.b64encode(b'[]').decode(), base64.b64encode(b'{"amount":1,"amount":2}').decode(),
    base64.b64encode(b'{"x":NaN}').decode(), base64.b64encode(b'\xff').decode(),
    base64.b64encode(b'{"x":' * 18 + b'1' + b'}' * 18).decode()])
def test_untrusted_http_header_rejected(encoded):
    with pytest.raises(ValueError):
        decode_header(encoded)


@pytest.mark.parametrize("key,value", [("network", "base"), ("network", "solana:mainnet"),
    ("amount", "1.0"), ("amount", "01"), ("amount", 10000), ("amount", "0"),
    ("amount", str(2**256)), ("maxTimeoutSeconds", True), ("maxTimeoutSeconds", 3601),
    ("asset", "0x" + "0" * 40), ("payTo", "platform"), ("scheme", "upto")])
def test_no_unsupported_or_ambiguous_payment_options(key, value):
    req = requirement()
    req[key] = value
    with pytest.raises(ValueError):
        validate_requirement(req)


@pytest.mark.parametrize("key,value", [("assetTransferMethod", "permit2"), ("paymentFlow", "upfront"),
                                     ("paymentFlow", "escrow"), ("name", ""), ("version", None)])
def test_no_silent_fallback_to_a_different_transfer_or_flow(key, value):
    req = requirement()
    req["extra"][key] = value
    with pytest.raises(ValueError):
        validate_requirement(req)


def test_no_v1_or_boolean_version_and_strict_authorization_terms():
    for version in (1, True, "2"):
        value = required()
        value["x402Version"] = version
        with pytest.raises(ValueError):
            validate_required(value)
    payload = signed()
    payload["payload"]["authorization"]["value"] = "10001"
    with pytest.raises(ValueError, match="TERMS_MISMATCH"):
        validate_payload(payload)
    payload = signed()
    with pytest.raises(ValueError, match="EXPIRED"):
        validate_payload(payload, now=1060)


@pytest.mark.parametrize("field,value", [("network", "eip155:8453"), ("asset", "0x" + "33" * 20),
    ("payTo", "0x" + "44" * 20), ("amount", "20000")])
def test_signature_is_bound_to_chain_token_recipient_amount(field, value):
    payload = signed()
    assert EvmVerifier().verify_signature(payload)
    payload["accepted"][field] = value
    if field == "payTo":
        payload["payload"]["authorization"]["to"] = value
    if field == "amount":
        payload["payload"]["authorization"]["value"] = value
    assert not EvmVerifier().verify_signature(payload)


def test_signature_recovers_against_independent_eip3009_abi_digest():
    payload = signed()
    req, auth = payload["accepted"], payload["payload"]["authorization"]
    # Independent ABI construction, using the type hash published by ERC-3009.
    typehash = bytes.fromhex("7c7c6cdb67a18743f49ec6fa9b35f50d52ed05cbed4cc592e13b44501c1a2267")
    domain = keccak(encode(["bytes32", "bytes32", "bytes32", "uint256", "address"],
        [keccak(text="EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"),
         keccak(text="USDC"), keccak(text="2"), 84532, req["asset"]]))
    struct = keccak(encode(["bytes32", "address", "address", "uint256", "uint256", "uint256", "bytes32"],
        [typehash, auth["from"], auth["to"], 10000, 0, 1060, bytes.fromhex(auth["nonce"][2:])]))
    recovered = Account._recover_hash(keccak(b"\x19\x01" + domain + struct), signature=payload["payload"]["signature"])
    assert recovered.lower() == auth["from"].lower()


def test_settlement_headers_cannot_switch_payer_or_network():
    payload = signed()
    receipt = {"success": True, "network": "eip155:84532", "payer": payload["payload"]["authorization"]["from"],
               "transaction": "0x" + "ab" * 32}
    assert validate_settlement(receipt, payload) == receipt
    for field, value in [("payer", "0x" + "44" * 20), ("network", "eip155:8453"), ("transaction", "")]:
        bad = copy.deepcopy(receipt)
        bad[field] = value
        with pytest.raises(ValueError):
            validate_settlement(bad, payload)
