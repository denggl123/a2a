"""Durable payment behavior and real HTTP adapters, with fixture ledger data.

All keys are unfunded test keys. No blockchain writes or real funds are used.
"""
import base64
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import time

import pytest

from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector
from a2n_node.x402.evm import EvmSigner, EvmVerifier, EvmObserver, EvmRPC, TRANSFER_TOPIC, USED_TOPIC, topic_address
from a2n_node.x402.http import HTTPClient, HTTPFacilitator, HTTPResponse
from a2n_node.x402.service import X402NodeService
from a2n_sdk.payments import PaymentBook, RiskBook
from a2n_sdk.storage import LocalStore
from a2n_sdk.x402 import HTTPResult, X402ResourceServer, encode_header, decode_header
from a2n_sdk.x402.protocol import PAYMENT_REQUIRED, PAYMENT_RESPONSE, PAYMENT_SIGNATURE
from .test_x402_protocol import requirement, required, signed


class Ledger:
    """JSON-RPC fixture: controlled receipts, reorgs and confirmation depth."""
    def __init__(self):
        self.chain, self.latest, self.timestamp, self.used = 84532, 100, 1000, False
        self.receipt, self.logs = None, []
        self.block_hash = "0x" + "bb" * 32
        self.calls = []

    def settle(self, payload):
        self.latest, self.used = 103, True
        auth, asset = payload["payload"]["authorization"], payload["accepted"]["asset"]
        tx = "0x" + "aa" * 32
        common = {"address": asset, "transactionHash": tx, "blockHash": self.block_hash}
        self.logs = [{**common, "topics": [USED_TOPIC, topic_address(auth["from"]), auth["nonce"]], "data": "0x"},
                     {**common, "topics": [TRANSFER_TOPIC, topic_address(auth["from"]), topic_address(auth["to"])],
                      "data": "0x" + hex(int(auth["value"]))[2:].rjust(64, "0")}]
        self.receipt = {"status": "0x1", "blockNumber": "0x65", "blockHash": self.block_hash,
                        "transactionHash": tx, "logs": self.logs}
        return {"success": True, "payer": auth["from"], "network": "eip155:84532", "transaction": tx}

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "eth_chainId": return hex(self.chain)
        if method == "eth_blockNumber": return hex(self.latest)
        if method == "eth_getTransactionReceipt": return copy.deepcopy(self.receipt)
        if method == "eth_getBlockByNumber": return {"hash": self.block_hash, "timestamp": hex(self.timestamp)}
        if method == "eth_getLogs":
            return copy.deepcopy([l for l in self.logs if l["topics"][0] == USED_TOPIC and l["topics"] == params[0]["topics"]])
        if method == "eth_call": return "0x" + ("0" * 63) + ("1" if self.used else "0")
        raise AssertionError(method)


def observer(ledger):
    req = requirement()
    return EvmObserver(ledger, network=req["network"], asset=req["asset"], name="USDC", version="2", confirmations=3)


def config(rpc_url="http://127.0.0.1:1/rpc"):
    req = requirement()
    return {"allow_http": True, "assets": [{"currency": "USDC", "network": req["network"],
        "asset": req["asset"], "name": "USDC", "version": "2", "rpc_url": rpc_url, "confirmations": 3}]}


class Facilitator:
    def __init__(self, ledger):
        self.ledger, self.verifies, self.settles = ledger, 0, 0
        self.lose_settle_response, self.reject_verify = False, False

    def supported(self):
        return {"kinds": [{"x402Version": 2, "scheme": "exact", "network": "eip155:84532"}],
                "extensions": [], "signers": {}}

    def verify(self, payload, req):
        self.verifies += 1
        return {"isValid": EvmVerifier().verify_signature(payload) and not self.reject_verify,
                "payer": payload["payload"]["authorization"]["from"]}

    def settle(self, payload, req):
        self.settles += 1
        receipt = self.ledger.settle(payload)
        if self.lose_settle_response:
            raise TimeoutError("response lost after broadcast")
        return receipt


@pytest.mark.parametrize("tamper", ["amount", "recipient", "nonce", "token", "payer", "status", "reorg", "depth", "chain"])
def test_only_matching_final_chain_events_confirm_a_payment(tamper):
    ledger, payload = Ledger(), signed()
    hint = ledger.settle(payload)
    if tamper == "amount": ledger.receipt["logs"][1]["data"] = hex(9999)
    if tamper == "recipient": ledger.receipt["logs"][1]["topics"][2] = topic_address("0x" + "44" * 20)
    if tamper == "nonce": ledger.receipt["logs"][0]["topics"][2] = "0x" + "ff" * 32
    if tamper == "token": ledger.receipt["logs"][1]["address"] = "0x" + "44" * 20
    if tamper == "payer": ledger.receipt["logs"][1]["topics"][1] = topic_address("0x" + "44" * 20)
    if tamper == "status": ledger.receipt["status"] = "0x0"
    if tamper == "reorg": ledger.block_hash = "0x" + "cc" * 32
    if tamper == "depth": ledger.latest = 101
    if tamper == "chain": ledger.chain = 8453
    assert observer(ledger).observe(payload, 100, hint)["state"] == "UNKNOWN"


def test_ledger_can_find_payment_without_server_receipt_but_unused_requires_chain_expiry():
    ledger, payload = Ledger(), signed()
    assert observer(ledger).observe(payload, 100)["state"] == "UNKNOWN"
    hint = ledger.settle(payload)
    assert observer(ledger).observe(payload, 100)["settlement"] == hint
    ledger = Ledger()
    ledger.latest, ledger.timestamp = 103, 1061
    result = observer(ledger).observe(payload, 100)
    assert result["state"] == "FAILED" and result["definitive"]
    ledger.used = True  # Consumed/canceled without transfer proof isn't a payment.
    assert observer(ledger).observe(payload, 100)["state"] == "UNKNOWN"
    ledger.used, ledger.latest = False, 12000
    assert observer(ledger).observe(payload, 100)["state"] == "UNKNOWN"


def test_seller_executes_and_settles_once_even_after_restart_and_replay():
    store, ledger = LocalStore(), Ledger()
    facilitator = Facilitator(ledger)
    gate = X402ResourceServer(store, facilitator, EvmVerifier(), observer(ledger), now=lambda: 1000)
    payload, executed = signed(), []
    challenge = gate.handle(required(), None, lambda: pytest.fail("unpaid execution"))
    assert challenge.status == 402 and decode_header(challenge.headers[PAYMENT_REQUIRED])["accepts"] == [requirement()]
    def execute():
        executed.append(True)
        return HTTPResult(200, b'{"value":3}')
    result = gate.handle(required(), encode_header(payload), execute, body=b"input", replay_token="private" * 8)
    assert result.status == 200 and PAYMENT_RESPONSE in result.headers
    restarted = X402ResourceServer(store, facilitator, EvmVerifier(), observer(ledger), now=lambda: 5000)
    assert restarted.handle(required(), encode_header(payload), execute, body=b"input", replay_token="private" * 8) == result
    assert restarted.handle(required(), encode_header(payload), execute, body=b"input").status == 409
    forged = copy.deepcopy(payload)
    forged["payload"]["signature"] = "0x" + "00" * 65
    with pytest.raises(ValueError, match="INVALID_SIGNATURE"):
        restarted.handle(required(), encode_header(forged), execute, body=b"input", replay_token="private" * 8)
    assert len(executed) == facilitator.verifies == facilitator.settles == 1
    with pytest.raises(ValueError, match="REPLAY"):
        restarted.handle(required(), encode_header(payload), execute, body=b"different input")


def test_seller_settlement_timeout_retains_result_and_only_reconciles():
    store, ledger = LocalStore(), Ledger()
    facilitator = Facilitator(ledger)
    facilitator.lose_settle_response = True
    gate = X402ResourceServer(store, facilitator, EvmVerifier(), observer(ledger), now=lambda: 1000)
    payload = signed()
    assert gate.handle(required(), encode_header(payload), lambda: HTTPResult(200, b"done"), replay_token="private" * 8).status == 503
    key = next(iter(store.items("x402_server_payments")))
    assert gate.reconcile(key)["state"] == "CONFIRMED"
    assert gate.handle(required(), encode_header(payload), lambda: pytest.fail("duplicate execution"), replay_token="private" * 8).body == b"done"
    assert facilitator.settles == 1


def test_seller_rejects_invalid_payment_or_failed_handler_without_settlement():
    store, ledger = LocalStore(), Ledger()
    facilitator = Facilitator(ledger)
    gate = X402ResourceServer(store, facilitator, EvmVerifier(), observer(ledger), now=lambda: 1000)
    facilitator.reject_verify = True
    assert gate.handle(required(), encode_header(signed()), lambda: pytest.fail("invalid execution")).status == 402
    facilitator.reject_verify = False
    assert gate.handle(required(), encode_header(signed()), lambda: HTTPResult(500, b"failed")).status == 500
    assert facilitator.settles == 0


def test_seller_process_death_during_execution_never_reexecutes_or_charges():
    store, ledger = LocalStore(), Ledger()
    facilitator = Facilitator(ledger)
    payload = signed()
    gate = X402ResourceServer(store, facilitator, EvmVerifier(), observer(ledger), now=lambda: 1000)
    def process_death():
        raise SystemExit("process terminated")
    with pytest.raises(SystemExit):
        gate.handle(required(), encode_header(payload), process_death, replay_token="private" * 8)
    restarted = X402ResourceServer(store, facilitator, EvmVerifier(), observer(ledger), now=lambda: 1000)
    key = next(iter(store.items("x402_server_payments")))
    assert restarted.reconcile(key)["state"] == "UNKNOWN"
    assert restarted.handle(required(), encode_header(payload), lambda: pytest.fail("reexecution"), replay_token="private" * 8).status == 503
    assert facilitator.verifies == 1 and facilitator.settles == 0


def test_concurrent_seller_retries_do_not_repeat_business_or_settlement():
    store, ledger = LocalStore(), Ledger()
    facilitator = Facilitator(ledger)
    gate = X402ResourceServer(store, facilitator, EvmVerifier(), observer(ledger), now=lambda: 1000)
    payload, enter, finish = signed(), threading.Event(), threading.Event()
    def handler():
        enter.set()
        assert finish.wait(3)
        return HTTPResult(200, b"done")
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(gate.handle, required(), encode_header(payload), handler, replay_token="private" * 8)
        assert enter.wait(3)
        retry = gate.handle(required(), encode_header(payload), lambda: pytest.fail("duplicate"), replay_token="private" * 8)
        assert retry.status == 503
        finish.set()
        assert first.result().status == 200
    assert facilitator.verifies == facilitator.settles == 1


class TimeoutHTTP:
    def __init__(self): self.requests = 0
    def validate_url(self, url): pass
    def request(self, *args, **kwargs):
        self.requests += 1
        raise TimeoutError("request lost")


def buyer(store=None, *, http=None, ledger=None):
    store, ledger = store or LocalStore(), ledger or Ledger()
    service = X402NodeService(store, config(), EvmSigner(bytes.fromhex("11" * 32)),
        http=http or TimeoutHTTP(), observers={"USDC": observer(ledger)})
    risk = RiskBook(store)
    if not risk.policy()["revision"]:
        risk.configure({"USDC": {"per_trade": 10000, "total_exposure": 20000, "daily_spend": 20000,
                                 "per_counterparty": 20000}}, expected_revision=0)
    book = PaymentBook(store, risk, service.driver)
    service.bind(book)
    return service, book, ledger


def prepare(service, **changes):
    body = {"required_header": encode_header(required()), "accepted": requirement(), "url": "https://seller.test/paid",
        "method": "POST", "body_base64": base64.b64encode(b'{"input":1}').decode(),
        "trade_uid": "trade-1", "counterparty_did": "did:a2n:seller", "command_id": "approve-1"}
    body.update(changes)
    return service.buyer.prepare(**body)


def test_buyer_timeout_retains_exposure_and_nonce_across_encrypted_restart(tmp_path):
    path, key, http = tmp_path / "payments.db", base64.b64encode(os.urandom(32)).decode(), TimeoutHTTP()
    store = LocalStore(path, EnvironmentProtector(key))
    service, book, ledger = buyer(store, http=http)
    row = prepare(service)
    assert book.submit(row["intent_id"])["state"] == "UNKNOWN"
    authorization = store.get("x402_authorizations", row["intent_id"])
    store.close()
    assert b'TransferWithAuthorization' not in path.read_bytes()
    reopened = LocalStore(path, EnvironmentProtector(key))
    service, book, _ = buyer(reopened, http=http, ledger=ledger)
    assert book.submit(row["intent_id"])["state"] == "UNKNOWN"
    assert reopened.get("x402_authorizations", row["intent_id"]) == authorization
    assert reopened.get("risk_reservations", row["intent_id"])["state"] == "ACTIVE"
    ledger.settle(authorization["payload"])
    assert book.reconcile(row["intent_id"])["state"] == "CONFIRMED"
    assert reopened.get("risk_reservations", row["intent_id"])["state"] == "CONSUMED"
    assert http.requests == 1
    assert service.buyer.outcome(row["intent_id"])["delivery"]["state"] == "UNKNOWN"
    reopened.close()


def test_buyer_does_not_confirm_a_successful_http_response_and_forged_receipt():
    class ForgedHTTP(TimeoutHTTP):
        def request(self, *args, **kwargs):
            self.requests += 1
            payload = decode_header(kwargs["headers"][PAYMENT_SIGNATURE])
            forged = {"success": True, "network": "eip155:84532", "transaction": "0x" + "aa" * 32,
                      "payer": payload["payload"]["authorization"]["from"]}
            return HTTPResponse(200, {PAYMENT_RESPONSE: encode_header(forged)}, b"delivered")
    http = ForgedHTTP()
    service, book, ledger = buyer(http=http)
    key = prepare(service)["intent_id"]
    assert book.submit(key)["state"] == "UNKNOWN"
    assert service.buyer.outcome(key)["delivery"]["state"] == "DELIVERED"
    assert service.store.get("risk_reservations", key)["state"] == "ACTIVE"
    assert book.reconcile(key)["state"] == "UNKNOWN" and http.requests == 1


def test_buyer_approval_pins_input_payee_and_budget_before_any_signature():
    service, book, ledger = buyer()
    row = prepare(service)
    assert prepare(service)["intent_id"] == row["intent_id"]
    with pytest.raises(ValueError, match="REQUEST_CONFLICT"):
        prepare(service, body_base64=base64.b64encode(b"changed").decode())
    with pytest.raises(ValueError, match="APPROVAL_MISMATCH"):
        prepare(service, url="https://other.test/paid")
    bad = requirement()
    bad["payTo"] = "0x" + "44" * 20
    with pytest.raises(ValueError, match="APPROVAL_MISMATCH"):
        prepare(service, accepted=bad)
    with pytest.raises(ValueError, match="RISK_LIMIT"):
        changed = requirement()
        changed["amount"] = "10001"
        challenge = required()
        challenge["accepts"] = [changed]
        prepare(service, trade_uid="large", command_id="large", accepted=changed, required_header=encode_header(challenge))
    assert not service.store.items("x402_authorizations")
    assert service.driver.http.requests == 0


def test_buyer_expiry_requires_read_only_ledger_evidence_to_release_exposure():
    service, book, ledger = buyer()
    key = prepare(service)["intent_id"]
    book.submit(key)
    auth = service.store.get("x402_authorizations", key)["payload"]["payload"]["authorization"]
    assert book.reconcile(key)["state"] == "UNKNOWN"
    ledger.latest, ledger.timestamp = 103, int(auth["validBefore"]) + 1
    assert book.reconcile(key)["state"] == "FAILED"
    assert service.store.get("risk_reservations", key)["state"] == "RELEASED"
    assert service.driver.http.requests == 1


@contextmanager
def fixture_http():
    ledger = Ledger()
    facilitator = Facilitator(ledger)
    state = {"redirect_hits": 0, "executions": 0, "gate": None, "url": ""}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self): self.dispatch()
        def do_POST(self): self.dispatch()
        def dispatch(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            result, headers, status = {}, {}, 200
            if self.path == "/supported": result = facilitator.supported()
            elif self.path == "/rpc":
                rpc = json.loads(body)
                result = {"jsonrpc": "2.0", "id": rpc["id"], "result": ledger.call(rpc["method"], rpc["params"])}
            elif self.path in {"/verify", "/settle"}:
                request = json.loads(body)
                assert request["x402Version"] == 2 and request["paymentPayload"]["accepted"] == request["paymentRequirements"]
                operation = facilitator.verify if self.path == "/verify" else facilitator.settle
                result = operation(request["paymentPayload"], request["paymentRequirements"])
            elif self.path == "/paid":
                def execute():
                    state["executions"] += 1
                    return HTTPResult(200, b'{"value":3}', {"Content-Type": "application/json"})
                reply = state["gate"].handle(required(state["url"] + "/paid"), self.headers.get(PAYMENT_SIGNATURE),
                    execute, method=self.command, body=body, replay_token=self.headers.get("Idempotency-Key"))
                result, headers, status = reply.body, reply.headers, reply.status
            elif self.path == "/redirect":
                status, headers = 307, {"Location": state["url"] + "/leak"}
            elif self.path == "/leak": state["redirect_hits"] += 1
            raw = result if isinstance(result, bytes) else json.dumps(result).encode()
            self.send_response(status)
            for k, v in headers.items(): self.send_header(k, v)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state["url"] = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try: yield state, facilitator, ledger
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_standard_http_roundtrip_with_signed_payment_and_independent_rpc_verification():
    with fixture_http() as (state, facilitator, ledger):
        http, url = HTTPClient(allow_http=True), state["url"]
        # The seller and buyer have distinct stores and independently observe RPC.
        seller = X402NodeService(LocalStore(), config(url + "/rpc"))
        state["gate"] = seller.resource_server(url)
        store = LocalStore()
        service = X402NodeService(store, config(url + "/rpc"), EvmSigner(bytes.fromhex("11" * 32)))
        risk = RiskBook(store)
        risk.configure({"USDC": {k: 10000 for k in ("per_trade", "total_exposure", "daily_spend", "per_counterparty")}}, expected_revision=0)
        book = PaymentBook(store, risk, service.driver)
        service.bind(book)
        challenge = http.request("POST", url + "/paid", body=b'{"input":1}')
        assert challenge.status == 402 and state["executions"] == 0
        key = service.buyer.prepare(required_header=challenge.headers[PAYMENT_REQUIRED], accepted=requirement(),
            url=url + "/paid", method="POST", body_base64=base64.b64encode(b'{"input":1}').decode(),
            trade_uid="http-trade", command_id="http-approve", counterparty_did="did:a2n:seller")["intent_id"]
        result = book.submit(key)
        assert result["state"] == "CONFIRMED" and result["amount_minor"] == 10000
        assert base64.b64decode(service.buyer.outcome(key)["delivery"]["body_base64"]) == b'{"value":3}'
        assert state["executions"] == facilitator.settles == facilitator.verifies == 1
        assert book.submit(key)["state"] == "CONFIRMED"
        assert state["executions"] == 1
        assert all(not method.startswith("eth_send") for method, _ in ledger.calls)


def test_http_never_forwards_payment_authorization_to_redirect_target():
    with fixture_http() as (state, _, __):
        http = HTTPClient(allow_http=True)
        result = http.request("POST", state["url"] + "/redirect", body=b"{}", headers={PAYMENT_SIGNATURE: "private"})
        assert result.status == 307 and state["redirect_hits"] == 0
    with pytest.raises(ValueError, match="HTTPS_REQUIRED"):
        HTTPClient().request("GET", "http://seller.test/paid")


def test_configured_node_payment_api_executes_approved_http_payment(tmp_path):
    with fixture_http() as (state, facilitator, ledger):
        seller = X402NodeService(LocalStore(), config(state["url"] + "/rpc"))
        state["gate"] = seller.resource_server(state["url"])
        protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
        node = Daemon(tmp_path / "buyer", port=0, protector=protector, beacon=False,
            x402_config=config(state["url"] + "/rpc"), x402_signer=EvmSigner(bytes.fromhex("11" * 32))).start()
        try:
            http = HTTPClient(allow_http=True)
            token = {"X-A2N-Local-Token": node.runtime.management_token, "Content-Type": "application/json"}
            base = node.runtime.local_base_url
            policy = {"expected_revision": 0, "limits": {"USDC": {k: 10000 for k in (
                "per_trade", "total_exposure", "daily_spend", "per_counterparty")}}}
            assert http.request("PUT", base + "/v1/risk", body=json.dumps(policy).encode(), headers={**token, "If-Match": "0"}).status == 200
            status = http.request("GET", base + "/v1/x402", headers=token)
            assert json.loads(status.body)["state"] == "CONFIGURED"
            challenge = http.request("POST", state["url"] + "/paid", body=b"{}")
            approval = {"required_header": challenge.headers[PAYMENT_REQUIRED], "accepted": requirement(),
                "url": state["url"] + "/paid", "method": "POST", "body_base64": base64.b64encode(b"{}").decode(),
                "trade_uid": "node-payment", "counterparty_did": "did:a2n:seller", "command_id": "node-approve"}
            response = http.request("POST", base + "/v1/x402/prepare", body=json.dumps(approval).encode(), headers=token)
            assert response.status == 201, response.body
            intent = json.loads(response.body)
            assert intent["state"] == "READY" and state["executions"] == 0
            response = http.request("POST", base + "/v1/x402/intents/" + intent["intent_id"] + "/submit", body=b"{}", headers=token)
            result = json.loads(response.body)
            assert response.status == 200 and result["payment"]["state"] == "CONFIRMED"
            assert result["delivery"]["state"] == "DELIVERED" and state["executions"] == 1
            assert b'"signature"' not in response.body and b'replay_token' not in response.body
            assert http.request("GET", base + "/v1/x402/intents/" + intent["intent_id"], headers=token).status == 200
        finally:
            node.stop()


def test_node_management_exposes_module_but_never_enables_default_payment(tmp_path):
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    node = Daemon(tmp_path / "node", port=0, protector=protector, beacon=False).start()
    try:
        http = HTTPClient(allow_http=True)
        base = node.runtime.local_base_url
        assert http.request("GET", base + "/v1/x402").status == 401
        result = http.request("GET", base + "/v1/x402", headers={"X-A2N-Local-Token": node.runtime.management_token})
        status = json.loads(result.body)
        assert result.status == 200 and status["implemented"] and status["state"] == "NOT_CONFIGURED"
        assert status["platform_commission_minor"] == 0 and not status["automatic_payment"]
        assert node.payments.capabilities()["state"] == "NOT_CONFIGURED"
    finally: node.stop()
