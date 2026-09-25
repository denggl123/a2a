"""Opaque relay envelopes and provider authentication are independent of A2A."""

import os

import pytest

from a2n_node.relay_crypto import (
    open_request, open_response, private_from_seed, public_b64,
    seal_request, seal_response,
)
from a2n_node.relay_service import PublicRelay, sign_auth
from a2n_p2p import Identity


def test_sealed_request_and_response_hide_payload_and_bind_route():
    private = private_from_seed(os.urandom(32))
    aad = b"a2n-relay/1|provider|service|a2a"
    value = {"task": "private", "data": "x" * 4000}
    envelope, caller_key = seal_request(public_b64(private), value, aad=aad)
    assert "private" not in str(envelope)
    opened, provider_key = open_request(private, envelope, aad=aad)
    assert opened == value and caller_key == provider_key
    sealed = seal_response(provider_key, {"status": 200, "body": value}, aad=aad)
    assert open_response(caller_key, sealed, aad=aad)["body"] == value
    with pytest.raises(Exception):
        open_request(private, envelope, aad=b"different-service")
    with pytest.raises(Exception):
        open_response(caller_key, sealed, aad=b"different-service")


def test_volunteer_relay_auth_binds_body_and_rejects_replay():
    relay = PublicRelay(lambda: "http://127.0.0.1:9876")
    provider = Identity.generate()
    body = {"provider_did": provider.did}
    proof = sign_auth(provider, "poll", body)
    assert relay.poll(body, proof, timeout=0) == {"job": None}
    with pytest.raises(PermissionError):
        relay.poll(body, proof, timeout=0)
    wrong = sign_auth(provider, "poll", body)
    with pytest.raises(PermissionError):
        relay.poll({"provider_did": "did:a2n:someone-else"}, wrong, timeout=0)
