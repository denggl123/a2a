"""B1: real signed invocation, public safe opinions, multi-source query and revisions."""
import base64
import json
import os
import time
import urllib.request

import pytest

from a2n_node.daemon import Daemon
from a2n_node.experience_gateway import envelope
from a2n_node.feedback_identity import signer_for
from a2n_node.protection import EnvironmentProtector
from a2n_sdk.experience import ExperienceBook, signed, unsigned
from a2n_sdk.ports import CallRequest
from a2n_sdk.storage import LocalStore


@pytest.fixture
def nodes(tmp_path):
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    values = [Daemon(tmp_path / str(i), port=0, protector=protector,
                     coord_allow_networks=["127.0.0.0/8"]).start() for i in range(3)]
    try:
        yield values
    finally:
        for node in reversed(values):
            node.stop()


def actual_feedback(nodes):
    provider, buyer, observer = nodes
    provider.runtime.mount_callable({"name": "实际输出", "version": "1",
        "skills": [{"id": "write", "name": "write"}]}, lambda p: {"text": p}, service_id="svc")
    card = provider.runtime.project_binding("svc")
    provider.management.discovery_public_base = provider.runtime.local_base_url
    buyer.runtime.import_agent(card, projection_id="use")
    result = buyer.calls.invoke("use", CallRequest(skill="write", task_id="trade", payload="actual"))
    assert result.ok and result.metadata["public_trade_anchor"]["buyer_did"] == buyer.identity.did
    assert result.metadata["trade_facts"]["execution"] == "DELIVERED"
    _, feedback = buyer.management.command("/v1/feedback/open", {"scope": "use", "task_id": "trade",
        "dimensions": {"quality": 2}, "note": "private business details"})
    publication = buyer.experience.publish(feedback["feedback_id"], public_note="联系 buyer@example.com")
    return provider, buyer, observer, feedback, publication


def subject(provider):
    return {"kind": "service", "provider_did": provider.identity.did, "service_id": "svc"}


def test_two_buyers_concurrently_reuse_task_number_without_collision_or_record_leak(nodes):
    from concurrent.futures import ThreadPoolExecutor
    from a2n_node import peer
    provider, first, second = nodes
    executed = []
    provider.runtime.mount_callable({"name": "unique parties", "version": "1",
        "skills": [{"id": "write", "name": "write"}]}, lambda p: executed.append(p) or p, service_id="svc")
    card = provider.runtime.project_binding("svc")
    for buyer in (first, second):
        buyer.runtime.import_agent(card, projection_id="use")
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda buyer: buyer.calls.invoke("use", CallRequest(task_id="same",
            skill="write", payload=buyer.identity.did)), (first, second)))
    assert all(r.ok and r.metadata["bilateral_ack_confirmed"] for r in results)
    assert len({r.metadata["trade_uid"] for r in results}) == 2
    assert len(executed) == 2 and provider.trials.status("svc")["completed"] == 2
    assert len(provider.trade_facts.list()) == 2 and len(provider.trials.samples("svc")) == 2
    for buyer in (first, second):
        retry = buyer.calls.invoke("use", CallRequest(task_id="same", skill="write", payload=buyer.identity.did))
        assert retry.result == buyer.identity.did
        proof = peer.sign_control(buyer.identity, provider_did=provider.identity.did,
            service_id="svc", task_id="same", method="tasks/get")
        body = {"jsonrpc": "2.0", "id": "read", "method": "tasks/get", "params": {"id": "same", "a2nPeerControl": proof}}
        req = urllib.request.Request(card["url"], data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=3) as response:
            read = json.load(response)["result"]
        assert read["id"] == "same" and read["artifacts"][0]["parts"][0]["text"] == buyer.identity.did
    assert len(executed) == 2


def wait_query(service, query_id):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        row = service.get(query_id)
        if row["state"] != "RUNNING":
            return row
        time.sleep(.02)
    raise AssertionError("query did not finish")


def test_real_trade_public_projection_excludes_private_note_and_input(nodes):
    provider, buyer, _, feedback, record = actual_feedback(nodes)
    assert buyer.experience.verify(record)
    buyer.experience.validate(record)
    text = json.dumps(record)
    assert "private business details" not in text and '"payload"' not in text
    assert "buyer@example.com" not in record["note"]
    assert record["trade_anchor"]["buyer_authorization"]["author_did"] == buyer.identity.did
    assert record["original_commitment"] != feedback["digest"]
    assert provider.trials.samples("svc")[0]["source_kind"] == "NODE_TRADE"
    assert "task_id" not in provider.management.public_samples("svc")["samples"][0]


def test_multisource_query_finds_author_feedback_even_when_seller_omits_it(nodes):
    provider, buyer, observer, _, record = actual_feedback(nodes)
    observer.public_directories.add(provider.runtime.local_base_url)
    observer.public_directories.add(buyer.runtime.local_base_url)
    query = observer.experience_service.start({"subject": subject(provider)}, "lookup")
    row = wait_query(observer.experience_service, query["query_id"])
    assert row["known_sources_complete"] and row["global_completeness"] == "UNKNOWN"
    assert row["received_count"] == 1 and len(row["completed_sources"]) == 2
    assert observer.experience.latest(subject(provider))[0]["record"] == record
    assert observer.experience.latest(subject(provider))[0]["eligible"]
    assert observer.experience_service.start({"subject": subject(provider)}, "lookup")["query_id"] == row["query_id"]
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        observer.experience_service.start({"subject": {"kind": "provider", "provider_did": provider.identity.did}}, "lookup")


def test_withdrawal_chain_deduplicates_copies_and_removes_public_contribution(nodes):
    provider, buyer, observer, feedback, record = actual_feedback(nodes)
    observer.experience.ingest(record, source="source-a")
    observer.experience.ingest(record, source="source-b")
    assert len(observer.experience.latest(subject(provider))) == 1
    withdrawn = buyer.experience.publish(feedback["feedback_id"], visibility="PARTIES_ONLY")
    assert withdrawn["publication_revision"] == 2 and withdrawn["visibility"] == "WITHDRAWN"
    observer.experience.ingest(withdrawn, source=buyer.identity.did)
    view = observer.experience.latest(subject(provider))[0]
    assert view["chain_complete"] and not view["eligible"]
    assert len(buyer.feedback.versions(feedback["feedback_id"])) == 1


def test_valid_signature_cannot_review_an_unrelated_buyer(nodes):
    provider, buyer, observer, _, record = actual_feedback(nodes)
    wrong = signed({**unsigned(record), "counterparty_did": observer.identity.did}, signer_for(buyer.identity))
    with pytest.raises(ValueError, match="FEEDBACK_ROLE_MISMATCH"):
        observer.experience.ingest(wrong, source="source")
    anchor = signed({**unsigned(record["trade_anchor"]), "buyer_did": observer.identity.did}, signer_for(provider.identity))
    wrong = signed({**unsigned(record), "trade_anchor": anchor}, signer_for(buyer.identity))
    with pytest.raises(ValueError, match="TRADE_ROLE_MISMATCH"):
        observer.experience.ingest(wrong, source="source")


def test_signed_revision_conflict_is_isolated_not_silently_chosen(nodes):
    provider, buyer, observer, _, record = actual_feedback(nodes)
    observer.experience.ingest(record, source="one")
    conflict = signed({**unsigned(record), "note": "different same revision"}, signer_for(buyer.identity))
    assert observer.experience.ingest(conflict, source="two")["conflict"]
    assert not observer.experience.latest(subject(provider))[0]["eligible"]


def test_payment_unavailable_is_rejected_before_upstream_execution(nodes):
    provider, buyer, _ = nodes
    calls = []
    card = {"name": "paid", "version": "1", "skills": [{"id": "write", "name": "write"}],
            "x-a2n": {"price_book": {"write": {"CNY": {"dimensions": [{"key": "call_count", "amount": 5, "per": 1}]}}}}}
    provider.runtime.mount_callable(card, lambda p: calls.append(p) or "delivered", service_id="paid")
    provider.store.put("trials", "paid", {"service_id": "paid", "completed": 10, "cap": 10})
    with pytest.raises(ValueError, match="PAYMENT_UNAVAILABLE"):
        provider.calls.invoke("paid", CallRequest(skill="write", task_id="no-funds", payload="job"))
    assert calls == [] and provider.store.task("paid", "no-funds") is None
    assert not provider.store.items("trial_admissions")


def test_snapshot_cursor_detects_public_revision_change(nodes):
    provider, buyer, _, feedback, first = actual_feedback(nodes)
    buyer.experience.publish(feedback["feedback_id"], public_note="changed")
    page = buyer.experience.page(subject(provider), limit=1)
    assert page["next_cursor"]
    buyer.experience.publish(feedback["feedback_id"], visibility="PARTIES_ONLY")
    with pytest.raises(ValueError, match="RESULT_CHANGED"):
        buyer.experience.page(subject(provider), limit=1, cursor=page["next_cursor"])


def test_public_gateway_authentication_replay_and_private_boundaries(nodes):
    provider, buyer, _, _, _ = actual_feedback(nodes)
    message = envelope(buyer.identity, "SUMMARY", provider.identity.did, {"subject": subject(provider)})
    status, response = provider.public_experience.handle("summary", message)
    assert status == 200 and provider.experience.verify(response)
    assert provider.public_experience.handle("summary", message)[0] == 409
    forged = {**message, "target_did": buyer.identity.did}
    assert provider.public_experience.handle("summary", forged)[0] == 401
    request = urllib.request.Request(provider.runtime.local_base_url + "/public/v1/experience/summary",
        data=json.dumps(envelope(buyer.identity, "SUMMARY", provider.identity.did,
            {"subject": subject(provider)})).encode(), headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=3) as result:
        assert json.loads(result.read())["body"]["published_count"] == 0


def test_incomplete_page_set_is_not_marked_fresh(nodes):
    provider, _, observer, _, record = actual_feedback(nodes)
    observer.experience.ingest(record, source="incomplete", source_complete=False)
    assert not observer.experience.latest(subject(provider))[0]["eligible"]
    observer.experience.confirm_source_page_set([record])
    assert observer.experience.latest(subject(provider))[0]["eligible"]


def test_real_public_feedback_rebuild_is_shadow_and_idempotent(nodes):
    provider, buyer, observer, _, record = actual_feedback(nodes)
    observer.experience.ingest(record, source=buyer.identity.did)
    first = observer.reputation.rebuild(subject(provider), current_version="1", command_id="calc")
    assert first["mode"] == "SHADOW" and first["dimensions"]["quality"]["effective_mass"] <= 1
    assert first["dimensions"]["quality"]["counterparty_groups"] == 1
    assert observer.reputation.rebuild(subject(provider), current_version="1", command_id="calc") == first
    second = observer.reputation.rebuild(subject(provider), current_version="1", command_id="calc2")
    assert not second["new_qualified_fact_hashes"]
