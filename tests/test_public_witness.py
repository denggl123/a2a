"""A voluntary witness stores signed hashes, not another transaction ledger."""

import pytest

from a2n_node import receipt
from a2n_node.witness import PublicWitness, attest, claim, verify_witness
from a2n_p2p import Identity
from a2n_sdk.storage import LocalStore


def _pair(provider, caller, task_id):
    signed = receipt.sign(provider, receipt.make_body(
        task_id=task_id, caller_did=caller.did, provider_did=provider.did,
        skill="private", input_hash=receipt.hash_payload("input"),
        output_hash=receipt.hash_payload("output")))
    digest = receipt.fingerprint(signed)
    return claim(digest, attest(provider, digest), attest(caller, digest))


def test_witness_is_idempotent_and_rate_bounded(monkeypatch):
    provider, caller, witness_id = Identity.generate(), Identity.generate(), Identity.generate()
    store = LocalStore()
    witness = PublicWitness(witness_id, store)
    first_claim = _pair(provider, caller, "task-0")
    first = witness.witness(first_claim)
    assert witness.witness(first_claim) == first
    assert store.count("public_witnesses") == 1
    assert verify_witness(first, receipt_hash=first_claim["receipt_hash"])
    assert verify_witness(first, signed_claim=first_claim)
    assert not verify_witness(first, signed_claim=_pair(provider, caller, "other-task"))
    assert "private" not in str(store.items("public_witnesses"))
    for i in range(1, 30):
        witness.witness(_pair(provider, caller, f"task-{i}"))
    with pytest.raises(ValueError, match="频繁"):
        witness.witness(_pair(provider, caller, "task-30"))
    monkeypatch.setattr("a2n_node.witness.MAX_WITNESSES", 30)
    another = Identity.generate()
    with pytest.raises(ValueError, match="容量"):
        witness.witness(_pair(provider, another, "another-task"))
    store.close()
