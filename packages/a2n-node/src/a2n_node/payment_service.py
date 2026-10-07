"""Node composition of bilateral terms, actual payment channels and Agent calls."""
from __future__ import annotations

import base64
from dataclasses import asdict
import json
import hmac
import secrets
import threading
import time
from eth_account import Account
from a2n_sdk.payment_coordination import (PaymentCoordinator, CAP_VERSION, OFFER_VERSION, PLAN_VERSION)
from a2n_sdk.experience import signed, unsigned, AUTH_VERSION
from a2n_sdk.contracts import input_digest
from a2n_sdk.ports import CallRequest, CallOutcome, CallResponse
from a2n_sdk.trade_facts import digest, delivered
from a2n_sdk.pricing import price_book, is_free
from a2n_sdk.payments import parse_minor
from a2n_sdk.x402.protocol import encode_header, PAYMENT_SIGNATURE, bounded_json
from a2n_sdk.x402.server import HTTPResult
from .card import card_did, verify_card
from .feedback_identity import signer_for, verifier_for
from .evm_payment import EvmNativeDriver, METHOD, wallet_binding, verify_wallet
from .x402.evm import EvmSigner
from .x402.http import HTTPClient
from .x402.service import X402NodeService
from .x402.refund import X402RefundDriver


class PaymentService:
    def __init__(self, daemon):
        self.daemon, self.store, self.payments = daemon, daemon.store, daemon.payments
        self.coordinator = PaymentCoordinator(self.store, self.payments, node_did=daemon.identity.did,
            signer=signer_for(daemon.identity), verifier=verifier_for())
        self._lock = threading.RLock()
        self._verified = threading.local()
        self.payments.on_change=self.observe_intent
        self.native = []
        self.config = self.store.get("payment_settings", "config") or {}
        self.signer = daemon.x402.driver.signer if daemon.x402.driver else None
        seed = self.store.get("payment_wallet", "key")
        if seed:
            self.signer = EvmSigner(seed)
        self.http = HTTPClient(allow_http=self.config.get("allow_http", False))
        for cfg in self.config.get("native", []):
            driver = EvmNativeDriver(self.store, cfg, self.signer)
            self.native.append(driver)
            self.payments.register(driver)
        self.gate = None
        self.refund_driver = None
        facilitator = self.config.get("facilitator_url")
        if facilitator and daemon.x402.driver:
            self.gate = daemon.x402.resource_server(facilitator)
            self.refund_driver=X402RefundDriver(self.store,daemon.x402.driver,facilitator)
            self.payments.register(self.refund_driver)
        for intent in self.store.items("payment_intents").values():
            self.observe_intent(intent)

    def observe_intent(self,intent):
        uid=intent["trade_uid"]
        refund="/refund/" in uid
        self.project_payment(uid.split("/refund/",1)[0] if refund else uid,intent["state"],refund=refund)

    def status(self):
        return {**self.coordinator.summary(), "wallet_address": self.signer.address if self.signer else None,
            "native": [{k: v for k, v in d.config.items() if k != "rpc_url"} for d in self.native],
            "x402": self.daemon.x402.status(), "provider_x402": self.gate is not None,
            "receiving": bool(self.signer and (self.native or self.gate)), "refunds": list(self.store.items("payment_refunds").values())[-50:],
            "incoming":[{"plan_id":p["plan"]["plan_id"],"trade_uid":p["plan"]["offer"]["trade_uid"],
                "amount_minor_decimal":str(p["plan"]["terms"]["amount_minor"]),"currency":p["plan"]["terms"]["currency"],"state":p["state"],
                "buyer_did":p["plan"]["offer"]["buyer_did"]} for p in list(self.store.items("payment_acceptances").values())[-100:]],
            "proposals":[{"plan_id":p["record"]["plan_id"],"state":p["state"],
                "amount_minor_decimal":str(p["record"]["terms"]["amount_minor"]),"currency":p["record"]["terms"]["currency"]}
                for p in list(self.store.items("payment_plans").values())[-100:] if p["state"]=="PROPOSED"]}

    def configure(self, body):
        if set(body) - {"config", "keystore", "password"}:
            raise ValueError("INVALID_PAYMENT_SETTINGS")
        cfg = body.get("config")
        if not isinstance(cfg, dict) or set(cfg) - {"native", "x402", "facilitator_url", "allow_http"}:
            raise ValueError("INVALID_PAYMENT_SETTINGS")
        if not isinstance(cfg.get("native", []), list) or len(cfg.get("native", [])) > 16:
            raise ValueError("INVALID_PAYMENT_SETTINGS")
        with self._lock:
            if (any(r["state"] not in {"CONFIRMED", "FAILED"} for r in self.store.items("payment_intents").values())
                    or any(r["state"] not in {"CONFIRMED", "FAILED", "REVOKED"} for r in self.store.items("payment_acceptances").values())):
                raise ValueError("PAYMENT_SETTINGS_LOCKED_BY_OPEN_ORDER")
            signer = self.signer
            key = None
            if body.get("keystore"):
                if len(json.dumps(body["keystore"])) > 65536:
                    raise ValueError("KEYSTORE_TOO_LARGE")
                key = Account.decrypt(body["keystore"], body.get("password", ""))
                signer = EvmSigner(key)
            native = [EvmNativeDriver(self.store, c, signer) for c in cfg.get("native", [])]
            if len({(d.config["network"], d.config["currency"]) for d in native}) != len(native):
                raise ValueError("DUPLICATE_NATIVE_PAYMENT_ASSET")
            x402 = X402NodeService(self.store, cfg.get("x402"), signer)
            http = HTTPClient(allow_http=cfg.get("allow_http", False))
            gate = x402.resource_server(cfg["facilitator_url"]) if cfg.get("facilitator_url") else None
            refund_driver=X402RefundDriver(self.store,x402.driver,cfg["facilitator_url"]) if gate else None
            with self.payments.atomic():
                self.store.put("payment_settings", "config", cfg)
                if key is not None:
                    self.store.put("payment_wallet", "key", "0x" + bytes(key).hex())
                self.payments.drivers = {}
                self.payments.driver = x402.driver if x402.driver and signer else None
                for d in [*native, *([x402.driver] if x402.driver else []), *([refund_driver] if refund_driver else [])]:
                    self.payments.register(d)
                x402.bind(self.payments)
                self.daemon.x402 = x402
                self.daemon.management.x402 = x402
                self.native, self.signer, self.config, self.gate, self.http = native, signer, dict(cfg), gate, http
                self.refund_driver=refund_driver
            self.daemon.management._sync_discovery()
            return self.status()

    def methods(self, *, receiving=False):
        if not self.signer:
            return []
        out = [d.descriptor() for d in self.native]
        driver = self.daemon.x402.driver
        if driver and (not receiving or self.gate):
            out += [{"method": "x402/2", "currency": c, "network": a["network"], "asset": a["asset"].lower(),
                "flow": "authorization"} for c, a in driver.assets.items()]
        return out

    def capability(self):
        return self.coordinator.capabilities(self.methods(), wallet_binding(self.signer, self.daemon.identity.did) if self.signer else None)

    def _driver(self, terms):
        if terms["method"] == METHOD:
            for d in self.native:
                if all(terms.get(k) == d.descriptor().get(k) for k in ("method", "network", "asset", "currency", "flow")):
                    return d
        elif terms["method"] == "x402/2":
            d = self.daemon.x402.driver
            if d and terms["currency"] in d.assets:
                a = d.assets[terms["currency"]]
                if (terms["network"], terms["asset"]) == (a["network"], a["asset"].lower()):
                    return d
        raise ValueError("PAYMENT_METHOD_NOT_CONFIGURED")

    def _remote(self, base, action, body):
        # Public hash-only quotes can use the signed node network's HTTP routes.
        # Payment authorizations and private recovery keep explicit TLS policy.
        transport=HTTPClient(allow_http=True) if action=="quote" else self.http
        transport.validate_url(base)
        return transport.json("POST", base.rstrip("/") + "/public/v1/payments/" + action, body)

    def quote(self, body):
        scope = body.get("projection_id")
        item = self.daemon.runtime.imported.get(scope)
        if not item or not verify_card(item.network_card)[0]:
            raise ValueError("SIGNED_NODE_AGENT_REQUIRED")
        request = CallRequest(**body.get("request", {}))
        if len(json.dumps(asdict(request),ensure_ascii=False,allow_nan=False).encode())>65536:
            raise ValueError("PAYMENT_COORDINATION_INPUT_TOO_LARGE: 单次输入上限 64 KiB")
        if request.metadata.get("a2nPaymentPlan"):
            raise ValueError("NEW_QUOTE_REQUIRES_UNBOUND_REQUEST")
        card = item.network_card
        sid = ((card.get("x-a2n") or {}).get("projection") or {}).get("service_id")
        if not sid or "/a2a/" not in str(card.get("url")):
            raise ValueError("PAYMENT_ENDPOINT_UNAVAILABLE")
        if not request.skill:
            request.skill = next((str(s.get("id") or "") for s in card.get("skills", []) if isinstance(s, dict)), "")
        request.metadata["a2nResolutionEndpoint"] = self.daemon.management.discovery_public_base or self.daemon.runtime.local_base_url
        query = self.coordinator.query(request, provider_did=card_did(card), service_id=sid, capabilities=self.capability())
        base = card["url"].rsplit("/a2a/", 1)[0]
        offer = self._remote(base, "quote", {"query": query, "skill": request.skill})
        self.coordinator.verify(offer, OFFER_VERSION, card_did(card), fresh=True)
        if (offer.get("query_digest") != digest(query) or offer.get("service_version") != str(card.get("version") or "")
                or any(offer.get(k)!=query[k] for k in ("buyer_did","provider_did","service_id","task_id","trade_uid","input_digest","skill"))
                or offer.get("platform_commission_minor") != 0):
            raise ValueError("PAYMENT_QUOTE_MISMATCH")
        self.store.put("payment_buyer_quotes", offer["offer_id"], {"offer": offer, "request": asdict(request), "scope": scope, "base": base})
        return offer

    def prepare(self, body):
        command = body.get("command_id")
        if not isinstance(command, str) or not 1 <= len(command) <= 128:
            raise ValueError("PAYMENT_COMMAND_ID_REQUIRED")
        fingerprint = digest(body)
        old = self.store.get("payment_prepare_commands", command)
        if old:
            if old["fingerprint"] != fingerprint:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            return self.store.get("payment_plans", old["plan_id"])
        quote = self.store.get("payment_buyer_quotes", body.get("offer_id", ""))
        if not quote:
            raise ValueError("PAYMENT_OFFER_NOT_FOUND")
        offer = quote["offer"]
        previous_call=self.store.task(quote["scope"],quote["request"]["task_id"])
        if previous_call:
            if not (previous_call.get("request",{}).get("metadata") or {}).get("a2nPaymentPlan"):
                raise ValueError("ORIGINAL_FREE_TRADE_CANNOT_BECOME_PAID")
            if not delivered(previous_call.get("outcome") or {}):
                raise ValueError("RECORDED_CALL_RECOVERY_REQUIRED")
        option_index=body.get("option_index")
        if isinstance(option_index,bool) or not isinstance(option_index,int) or not 0 <= option_index < len(offer["options"]):
            raise ValueError("PAYMENT_OPTION_UNAVAILABLE")
        terms = offer["options"][option_index]
        if previous_call and terms["flow"]!="upfront":
            raise ValueError("DELIVERED_CALL_REQUIRES_SETTLEMENT_ONLY_CHANNEL: 已交付任务只允许补付款，不再执行")
        verify_wallet(terms["wallet_binding"], offer["provider_did"])
        if terms["payee"].lower() != terms["wallet_binding"]["address"].lower():
            raise ValueError("PAYMENT_PAYEE_BINDING_MISMATCH")
        driver = self._driver(terms)
        plan = self.coordinator.plan(offer, option_index=body["option_index"],
            fee_cap_minor=parse_minor(body.get("fee_cap_minor", 0)), driver_id=driver.driver_id)
        # Save routing before IO; an uncertain acceptance can be recovered with the same plan.
        row = self.store.get("payment_plans", plan["plan_id"])
        self.store.put("payment_plans", plan["plan_id"], {**row, "request": quote["request"], "scope": quote["scope"], "base": quote["base"]})
        self.store.put("payment_prepare_commands", command, {"fingerprint": fingerprint, "plan_id": plan["plan_id"]})
        return self.activate(plan["plan_id"], command)

    def activate(self, plan_id, command):
        row = self.store.get("payment_plans", plan_id)
        if not row:
            raise ValueError("PAYMENT_PLAN_NOT_FOUND")
        if row.get("intent_id"):
            return row
        acceptance = self._remote(row["base"], "accept", {"plan": row["record"]})
        self.coordinator.activate(plan_id, acceptance, command_id=command)
        return self.store.get("payment_plans", plan_id)

    def _row(self, plan_id):
        row = self.store.get("payment_plans", plan_id)
        if not row or not row.get("intent_id"):
            raise ValueError("ACCEPTED_PAYMENT_PLAN_REQUIRED")
        return row

    def pay(self, plan_id, *, reconcile=False):
        row = self._row(plan_id)
        if row["record"]["terms"]["method"] != METHOD:
            raise ValueError("X402_AUTHORIZATION_EXECUTES_WITH_RESOURCE")
        intent = self.payments.get(row["intent_id"])
        if intent["state"] == "READY" and not reconcile:
            self.coordinator.verify(row["record"], PLAN_VERSION, fresh=True)
        result = self.payments.reconcile(row["intent_id"]) if reconcile else self.payments.submit(row["intent_id"])
        if result["state"] in {"CONFIRMED", "FAILED"}:
            self._remote(row["base"], "observe", {"plan": row["record"], "reference": result.get("reference")}) if result.get("reference") else None
        self.project_payment(row["record"]["offer"]["trade_uid"], result["state"])
        return result

    def execute(self, plan_id):
        row = self._row(plan_id)
        intent = self.payments.get(row["intent_id"])
        if row["record"]["terms"]["method"] == METHOD and intent["state"] != "CONFIRMED":
            raise ValueError("CONFIRMED_UPFRONT_PAYMENT_REQUIRED")
        if intent["state"] == "READY":
            self.coordinator.verify(row["record"], PLAN_VERSION, fresh=True)
        request = CallRequest(**row["request"])
        request.metadata["a2nPaymentPlan"] = row["record"]
        previous=self.store.task(row["scope"],request.task_id)
        previous_plan=((previous or {}).get("request",{}).get("metadata") or {}).get("a2nPaymentPlan")
        if previous_plan and previous_plan["plan_id"]!=plan_id:
            if not delivered(previous.get("outcome") or {}) or row["record"]["terms"]["flow"]!="upfront":
                raise ValueError("RECORDED_CALL_RECOVERY_REQUIRED")
            original=CallOutcome(**previous["outcome"])
            original.settlement={"state":intent["state"],"reference":intent.get("reference")}
            original.metadata["supplemental_payment_acceptance"]=row["acceptance"]
            self.daemon.calls.observe_external_delivery(row["scope"],request.task_id,original)
            return original.to_dict()
        return self.daemon.calls.invoke(row["scope"], request).to_dict()

    def free_execute(self, offer_id):
        row = self.store.get("payment_buyer_quotes", offer_id)
        if not row or row["offer"]["payment_state"] != "NOT_REQUIRED":
            raise ValueError("FREE_QUOTE_REQUIRED")
        self.coordinator.verify(row["offer"], OFFER_VERSION, fresh=True)
        return self.daemon.calls.invoke(row["scope"], CallRequest(**row["request"])).to_dict()

    def reconcile_x402(self, plan_id):
        row = self._row(plan_id)
        result = self.payments.reconcile(row["intent_id"])
        self.project_payment(row["record"]["offer"]["trade_uid"], result["state"])
        payment_plan = self.store.get("x402_plans", row["intent_id"])
        if not payment_plan:
            return {"payment":result,"delivery":None}
        token = payment_plan["replay_token"]
        record = signed({"v":"a2n-payment-recovery/1", "author_did":self.daemon.identity.did,
            "plan_id":plan_id,"token_digest":digest(token),"issued_at":time.time(),"expires_at":time.time()+60},signer_for(self.daemon.identity))
        recovered = self._remote(row["base"],"recover",{"record":record,"token":token})
        self.coordinator.verify(recovered,"a2n-payment-recovery-result/1",row["record"]["offer"]["provider_did"])
        if recovered.get("plan_id") != plan_id:
            raise ValueError("PAYMENT_RECOVERY_MISMATCH")
        delivery = recovered.get("delivery")
        if delivery:
            outcome = CallOutcome(**bounded_json(base64.b64decode(delivery["body_base64"],validate=True)))
            outcome = self.confirm_outcome(outcome, CallRequest(**self.store.task(row["scope"],row["request"]["task_id"])["request"]),row)
            outcome.settlement={"state":result["state"],"reference":result.get("reference")}
            self.daemon.calls.observe_external_delivery(row["scope"],row["request"]["task_id"],outcome)
            self.store.put("x402_deliveries",row["intent_id"],{"state":"DELIVERED",**delivery})
        return {"payment":result,"delivery":delivery}

    def invoke_x402(self, scope, request):
        plan = request.metadata["a2nPaymentPlan"]
        row = self._row(plan["plan_id"])
        key = row["intent_id"]
        driver = self._driver(plan["terms"])
        from a2n_sdk.upstream import a2a_message
        from .peer_exchange import signed_a2a_input
        from . import peer
        wire = asdict(request)
        wire["message"] = a2a_message(request)
        proof = peer.sign_request(self.daemon.identity,provider_did=plan["offer"]["provider_did"],
            skill=request.skill,payload=signed_a2a_input(wire["message"],request.context_id,request.metadata),
            task_id=request.task_id,service_id=plan["offer"]["service_id"])
        proof.pop("payload")
        wire["metadata"] = {**request.metadata,"a2nPeerRequest":proof}
        raw = json.dumps({"plan": plan, "request": wire}, ensure_ascii=False, allow_nan=False).encode()
        with self.payments.atomic():
            if not self.store.get("x402_plans", key):
                self.store.put("x402_plans", key, {"intent_id": key, "required": plan["terms"]["required"],
                    "accepted": plan["terms"]["required"]["accepts"][0], "method": "POST",
                    "url": plan["terms"]["required"]["resource"]["url"], "body_base64": base64.b64encode(raw).decode(),
                    "replay_token": secrets.token_hex(32), "fingerprint": digest(raw.decode())})
        result = self.payments.submit(key)
        self.project_payment(plan["offer"]["trade_uid"],result["state"])
        delivery = self.store.get("x402_deliveries", key) or {}
        outcome = None
        if delivery.get("state") == "DELIVERED":
            try:
                outcome = CallOutcome(**bounded_json(base64.b64decode(delivery["body_base64"])))
            except (ValueError, TypeError):
                pass
        if outcome is None:
            outcome = CallOutcome(False, request.task_id, "DELIVERY_UNKNOWN", error="支付或交付结果待核对，未重新执行")
        else:
            outcome = self.confirm_outcome(outcome,request,row)
        outcome.task_id = request.task_id
        outcome.settlement = {"state": result["state"], "reference": result.get("reference"), "driver_id": driver.driver_id}
        return outcome

    def confirm_outcome(self,outcome,request,row):
        from .peer_transport import SignedA2ATransport
        response=CallResponse(outcome.ok,outcome.result,outcome.state,outcome.error,outcome.usage,outcome.receipt,dict(outcome.metadata))
        plan=row["record"]
        transport=SignedA2ATransport(self.daemon.identity,feedback=self.daemon.feedback,anchor_validator=self.daemon.experience.validate_anchor)
        confirmed=transport._confirm(response,request,plan["offer"]["provider_did"],plan["offer"]["service_id"],row["base"],request.skill)
        outcome.ok,outcome.state,outcome.error=confirmed.ok,confirmed.state,confirmed.error
        outcome.receipt,outcome.metadata=confirmed.receipt,confirmed.metadata
        return outcome

    def validate_admission(self, scope, request, buyer):
        plan = request.metadata.get("a2nPaymentPlan")
        row = self.store.get("payment_acceptances", (plan or {}).get("plan_id", ""))
        if not row or row["plan"] != plan:
            raise ValueError("ACCEPTED_PAYMENT_PLAN_REQUIRED")
        if row.get("revocation") or row["state"]=="REVOKED":
            raise ValueError("PAYMENT_PLAN_REVOKED")
        offer = plan["offer"]
        if (buyer != offer["buyer_did"] or scope != offer["service_id"]
                or request.skill != offer["skill"]
                or (request.metadata.get("_a2n_wire_task_id") or request.task_id) != offer["task_id"]
                or input_digest(request) != offer["input_digest"]):
            raise ValueError("PAID_REQUEST_MISMATCH")
        if plan["terms"]["flow"] == "upfront" and row["state"] != "CONFIRMED":
            raise ValueError("PROVIDER_PAYMENT_CONFIRMATION_REQUIRED")
        if plan["terms"]["flow"] == "authorization" and getattr(self._verified, "plan_id", None) != plan["plan_id"]:
            raise ValueError("X402_VERIFIED_RESOURCE_REQUIRED")
        return plan

    def project_payment(self, uid, state, *, refund=False):
        self.daemon.trade_facts.financial(uid, state, refund=refund)

    def public(self, action, body, *, signature=None, replay_token=None):
        if action == "recover":
            record=self.coordinator.verify(body["record"],"a2n-payment-recovery/1",fresh=True)
            row=self.store.get("payment_acceptances",record["plan_id"])
            if not row or row["plan"]["offer"]["buyer_did"] != record["author_did"] or digest(body["token"]) != record["token_digest"]:
                raise ValueError("INVALID_PAYMENT_RECOVERY")
            key=self.store.get("payment_x402_keys",record["plan_id"])
            server=self.store.get("x402_server_payments",key or "")
            if not server or not self.gate or not hmac.compare_digest(server.get("replay_token_digest", ""),record["token_digest"]):
                raise ValueError("PAYMENT_RECOVERY_TOKEN_MISMATCH")
            server=self.gate.reconcile(key)
            payment_state=server["state"]
            if server["state"] == "UNKNOWN" and "anchor" in server:
                observed=self.daemon.x402.driver.observe(server["payload"],server["anchor"],server.get("settlement_hint"))
                if observed["state"]=="FAILED" and observed.get("definitive") is True:
                    payment_state="FAILED"
            if payment_state in {"CONFIRMED","FAILED"}:
                self.store.put("payment_acceptances",record["plan_id"],{**row,"state":payment_state})
                self.project_payment(row["plan"]["offer"]["trade_uid"],payment_state)
            return 200,signed({"v":"a2n-payment-recovery-result/1","author_did":self.daemon.identity.did,
                "plan_id":record["plan_id"],"payment_state":payment_state,"delivery":server.get("result")},signer_for(self.daemon.identity))
        if action == "revoke":
            record = self.coordinator.verify(body["record"], "a2n-payment-revocation/1")
            row = self.store.get("payment_acceptances", record["plan_id"])
            if not row or record["author_did"] != row["plan"]["offer"]["buyer_did"]:
                raise ValueError("INVALID_PAYMENT_REVOCATION")
            with self.store.tx():
                row = self.store.get("payment_acceptances", record["plan_id"])
                if row["state"] not in {"ACCEPTED", "REVOKED"} or self.store.get("payment_x402_keys",record["plan_id"]):
                    raise ValueError("PAYMENT_ORDER_LOCKED")
                self.store.put("payment_acceptances", record["plan_id"], {**row, "state": "REVOKED", "revocation": record})
                return 200, signed({"v":"a2n-payment-revocation-ack/1", "author_did":self.daemon.identity.did,
                    "plan_id":record["plan_id"], "revocation_digest":digest(record)}, signer_for(self.daemon.identity))
        if action == "refund-observe":
            record = self.coordinator.verify(body["record"], "a2n-payment-refund/1")
            original = self._row(record["original_plan_id"])["record"]
            if record["author_did"] != original["offer"]["provider_did"]:
                raise ValueError("REFUND_PARTY_MISMATCH")
            agreement = self.store.get("resolution_agreements", record["proposal_id"])
            if (not agreement or agreement["state"] != "BOTH_ACCEPTED" or agreement["proposal"]["body"]["action"] != "REFUND"
                    or digest(agreement["proposal"]) != record["proposal_digest"]):
                raise ValueError("BILATERAL_REFUND_AGREEMENT_REQUIRED")
            driver = self._driver(original["terms"])
            if original["terms"]["method"]==METHOD:
                result = driver.observe(reference=record["reference"], payer=original["terms"]["payee"], payee=original["payer"],
                    amount_minor=agreement["proposal"]["body"]["amount_minor"], plan_id="refund:"+record["proposal_digest"])
            else:
                payload=record["payload"]
                from .x402.evm import EvmVerifier
                auth=payload["payload"]["authorization"]
                terms=original["terms"]
                if (not EvmVerifier().verify_signature(payload) or auth["from"].lower()!=terms["payee"].lower()
                        or auth["to"].lower()!=original["payer"].lower() or int(auth["value"])!=agreement["proposal"]["body"]["amount_minor"]
                        or payload["accepted"]["network"]!=terms["network"] or payload["accepted"]["asset"].lower()!=terms["asset"]):
                    raise ValueError("REFUND_AUTHORIZATION_MISMATCH")
                result=driver.observe(payload,record["anchor"],{"success":True,"network":terms["network"],"transaction":record["reference"]})
            self.project_payment(original["offer"]["trade_uid"], result["state"], refund=True)
            return 200, signed({"v":"a2n-refund-observation/1", "author_did":self.daemon.identity.did,
                "proposal_id":record["proposal_id"], "observation":result}, signer_for(self.daemon.identity))
        if action == "quote":
            query = body["query"]
            self.coordinator.verify(query, "a2n-payment-query/1", fresh=True)
            self.coordinator.verify(query["capabilities"], CAP_VERSION, query["author_did"], fresh=True)
            if query["capabilities"].get("wallet_binding"):
                verify_wallet(query["capabilities"]["wallet_binding"], query["author_did"])
            scope = query["service_id"]
            binding = self.daemon.runtime.bindings.get(scope)
            if not binding:
                raise ValueError("AGENT_NOT_FOUND")
            card = binding.source_card
            free = "FREE_INITIAL" if not self.daemon.trials.status(scope)["ended"] else "FREE_VOLUNTARY" if is_free(card) else "FREE_RECONNECT" if self.daemon.reconnect.available(scope) else ""
            options = []
            for method in self.methods(receiving=True):
                entries = price_book(card).get(query["skill"], {}).get(method["currency"], [])
                # Metered post-delivery billing needs a separately supported contract.
                if len(entries) != 1 or entries[0]["key"] != "call_count" or entries[0]["per"] != 1 or not entries[0]["amount"]:
                    continue
                option = {**method, "amount_minor": entries[0]["amount"], "payee": self.signer.address,
                    "wallet_binding": wallet_binding(self.signer, self.daemon.identity.did),
                    "amount_minor_decimal":str(entries[0]["amount"]),
                    "platform_commission_minor": 0, "refund": "EXPLICIT_SEPARATE_PAYMENT", "fee_payer": "BUYER" if method["method"] == METHOD else "FACILITATOR"}
                if method["method"] == "x402/2":
                    asset = self.daemon.x402.driver.assets[method["currency"]]
                    required = {"x402Version": 2, "resource": {"url": self.daemon.management.discovery_public_base.rstrip("/") + "/public/v1/payments/execute", "mimeType": "application/json"},
                        "accepts": [{"scheme": "exact", "network": asset["network"], "asset": asset["asset"], "amount": str(option["amount_minor"]),
                            "payTo": option["payee"], "maxTimeoutSeconds": 300, "extra": {"name": asset["name"], "version": asset["version"]}}]}
                    self.gate.challenge(required)  # Advertise only facilitator-supported terms.
                    option["required"] = required
                options.append(option)
            return 200, self.coordinator.offer(query, source_card=card, options=options, free_reason=free)
        if action == "accept":
            plan = body["plan"]
            self.coordinator.verify(plan, PLAN_VERSION)
            with self.store.tx():
                previous=self.store.get("payment_acceptances",plan["plan_id"])
                if previous:
                    if previous["plan"]!=plan:
                        raise ValueError("PAYMENT_PLAN_MISMATCH")
                    return 200,previous["record"]
            self.coordinator.verify(plan, PLAN_VERSION, fresh=True)
            offer = plan["offer"]
            verify_wallet(offer["buyer_capabilities"]["wallet_binding"], offer["buyer_did"])
            with self.store.tx():
                binding = self.daemon.runtime.bindings.get(offer["service_id"])
                if not binding or digest(binding.source_card) != offer["source_card_digest"]:
                    raise ValueError("AGENT_PRICE_CHANGED")
                task = self.daemon.peer_exchange.journal_task_id(offer["service_id"], offer["task_id"], offer["buyer_did"])
                admission = self.daemon.trials.admit(offer["service_id"], task, caller_did=offer["buyer_did"],
                    provider_did=self.daemon.identity.did, voluntary_free=is_free(binding.source_card))
                if admission["free"]:
                    raise ValueError("FREE_SERVICE_REQUIRES_NEW_QUOTE")
                return 200, self.coordinator.accept(plan)
        if action == "observe":
            plan = body["plan"]
            row = self.store.get("payment_acceptances", plan["plan_id"])
            if not row or row["plan"] != plan:
                raise ValueError("PAYMENT_PLAN_NOT_FOUND")
            driver = self._driver(plan["terms"])
            if plan["terms"]["method"] != METHOD:
                raise ValueError("WRONG_PAYMENT_OBSERVER")
            result = driver.observe(reference=body["reference"], payer=plan["payer"], payee=plan["terms"]["payee"],
                amount_minor=plan["terms"]["amount_minor"], plan_id=plan["plan_id"])
            with self.store.tx():
                current = self.store.get("payment_acceptances", plan["plan_id"])
                if current["state"] != "CONFIRMED":
                    self.store.put("payment_acceptances", plan["plan_id"], {**current, "state": result["state"], "observation": result})
                self.project_payment(plan["offer"]["trade_uid"], result["state"])
            return 200, signed({"v": "a2n-payment-observation/1", "author_did": self.daemon.identity.did,
                "plan_id": plan["plan_id"], "observation": result}, signer_for(self.daemon.identity))
        if action == "execute":
            plan = body["plan"]
            if not self.gate or plan["terms"]["method"] != "x402/2":
                raise ValueError("X402_RESOURCE_NOT_CONFIGURED")
            accepted = self.store.get("payment_acceptances", plan["plan_id"])
            if not accepted or accepted["plan"] != plan:
                raise ValueError("PAYMENT_PLAN_NOT_FOUND")
            if accepted.get("revocation") or accepted["state"]=="REVOKED":
                raise ValueError("PAYMENT_PLAN_REVOKED")
            request = CallRequest(**body["request"])
            if request.metadata.get("a2nPaymentPlan") != plan:
                raise ValueError("PAID_REQUEST_MISMATCH")
            from a2n_sdk.x402.protocol import decode_header
            if signature:
                payload = decode_header(signature)
                if payload["payload"]["authorization"]["from"].lower() != plan["payer"].lower():
                    raise ValueError("X402_PAYER_MISMATCH")
                from .x402.evm import EvmVerifier
                if not EvmVerifier().verify_signature(payload):
                    raise ValueError("X402_INVALID_SIGNATURE")
                if payload["accepted"] != plan["terms"]["required"]["accepts"][0]:
                    raise ValueError("X402_PLAN_REQUIREMENT_MISMATCH")
                auth=payload["payload"]["authorization"]
                key=digest([payload["accepted"]["network"],payload["accepted"]["asset"].lower(),auth["from"].lower(),auth["nonce"].lower()])
                with self.store.tx():
                    current=self.store.get("payment_acceptances",plan["plan_id"])
                    if current.get("revocation") or current["state"]=="REVOKED":
                        raise ValueError("PAYMENT_PLAN_REVOKED")
                    prior_key=self.store.get("payment_x402_keys",plan["plan_id"])
                    if prior_key and prior_key != key:
                        raise ValueError("PAYMENT_ORDER_LOCKED: 不允许为同一计划换付款授权")
                    self.store.put("payment_x402_keys",plan["plan_id"],key)
            def invoke():
                request.metadata["_a2n_verified_peer"] = self.daemon.peer_exchange.authenticate(
                    plan["offer"]["service_id"],request.metadata.get("a2nPeerRequest"),message=request.message,
                    context_id=request.context_id,metadata=request.metadata,task_id=request.task_id,skill=request.skill)
                self._verified.plan_id = plan["plan_id"]
                try:
                    result = self.daemon.calls.invoke(plan["offer"]["service_id"], request).to_dict()
                    return HTTPResult(200, json.dumps(result, ensure_ascii=False).encode(), {})
                finally:
                    self._verified.plan_id = None
            raw = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
            result = self.gate.handle(plan["terms"]["required"], signature, invoke, method="POST", body=raw, replay_token=replay_token)
            # Monetary facts follow the independently observed server record.
            if signature:
                auth=payload["payload"]["authorization"]
                key=digest([payload["accepted"]["network"],payload["accepted"]["asset"].lower(),auth["from"].lower(),auth["nonce"].lower()])
                server=self.store.get("x402_server_payments",key)
                if server:
                    self.store.put("payment_x402_keys",plan["plan_id"],key)
                    state="CONFIRMED" if server["state"]=="CONFIRMED" else "UNKNOWN"
                    self.store.put("payment_acceptances",plan["plan_id"],{**accepted,"state":state})
                    self.project_payment(plan["offer"]["trade_uid"],state)
            return result
        raise ValueError("UNKNOWN_PAYMENT_OPERATION")

    def refund(self, body, *, reconcile=False):
        proposal_id = body["proposal_id"]
        agreement = self.store.get("resolution_agreements", proposal_id)
        if not agreement or agreement["state"] != "BOTH_ACCEPTED" or agreement["proposal"]["body"]["action"] != "REFUND":
            raise ValueError("BILATERAL_REFUND_AGREEMENT_REQUIRED")
        proposal = agreement["proposal"]
        uid = proposal["trade_uid"]
        original_id = self.store.get("payment_provider_orders", uid)
        accepted = self.store.get("payment_acceptances", original_id or "")
        if not accepted or accepted["state"] != "CONFIRMED":
            raise ValueError("CONFIRMED_ORIGINAL_PAYMENT_REQUIRED")
        plan = accepted["plan"]
        if proposal["body"]["currency"] != plan["terms"]["currency"]:
            raise ValueError("REFUND_CURRENCY_MISMATCH")
        driver = self._driver(plan["terms"]) if plan["terms"]["method"]==METHOD else self.refund_driver
        if not driver:
            raise ValueError("REFUND_CHANNEL_NOT_CONFIGURED")
        amount = proposal["body"]["amount_minor"]
        if plan["terms"]["method"]=="x402/2" and parse_minor(body.get("fee_cap_minor",0)):
            raise ValueError("X402_BUYER_FEE_MUST_BE_ZERO")
        row = self.store.get("payment_refunds", proposal_id)
        if not row:
            prior = sum(r["amount_minor"] for r in self.store.items("payment_refunds").values()
                if r["trade_uid"] == uid and self.payments.get(r["intent_id"])["state"] != "FAILED")
            if amount <= 0 or prior + amount > plan["terms"]["amount_minor"]:
                raise ValueError("REFUND_EXCEEDS_ORIGINAL_PAYMENT")
            with self.payments.atomic():
                intent = self.payments.create(trade_uid=uid+"/refund/"+proposal_id,currency=plan["terms"]["currency"],
                    amount_minor=amount,payee=plan["payer"],counterparty_did=plan["offer"]["buyer_did"],
                    command_id="refund:"+proposal_id,driver_id=driver.driver_id,plan_id="refund:"+digest(proposal),
                    fee_cap_minor=parse_minor(body.get("fee_cap_minor",0)))
                row={"proposal_id":proposal_id,"trade_uid":uid,"original_plan_id":original_id,
                    "intent_id":intent["intent_id"],"amount_minor":amount}
                self.store.put("payment_refunds",proposal_id,row)
                if plan["terms"]["method"]=="x402/2":
                    required=json.loads(json.dumps(plan["terms"]["required"]))
                    req=required["accepts"][0]
                    req["amount"],req["payTo"]=str(amount),plan["payer"]
                    self.store.put("x402_refund_plans",intent["intent_id"],{"required":required,"accepted":req})
        result = self.payments.reconcile(row["intent_id"]) if reconcile else self.payments.submit(row["intent_id"])
        self.project_payment(uid,result["state"],refund=True)
        self.store.put("resolution_agreements",proposal_id,{**agreement,"execution":"REFUND_"+result["state"]})
        if result.get("reference"):
            extra={}
            if plan["terms"]["method"]=="x402/2":
                authorization=self.store.get("x402_refund_authorizations",row["intent_id"])
                extra={"payload":authorization["payload"],"anchor":authorization["anchor"]}
            record = signed({"v":"a2n-payment-refund/1","author_did":self.daemon.identity.did,
                "original_plan_id":original_id,"proposal_id":proposal_id,"proposal_digest":digest(proposal),
                "reference":result["reference"],**extra},signer_for(self.daemon.identity))
            facts = self.daemon.trade_facts.get(uid)
            try:
                self._remote(facts["counterparty_endpoint"],"refund-observe",{"record":record})
            except Exception:
                self.store.put("payment_refunds",proposal_id,{**row,"notification":"PENDING"})
        return result

    def command(self, path, body):
        with self._lock:
            if path == "/v1/payment-coordination/configure":
                return 200, self.configure(body)
            if path == "/v1/payment-coordination/quote":
                return 200, self.quote(body)
            if path == "/v1/payment-coordination/prepare":
                return 201, self.prepare(body)
            if path == "/v1/payment-coordination/budget":
                limits=dict(self.daemon.risk.policy()["limits"])
                currency=body["currency"]
                values={k:parse_minor(body[k]) for k in ("per_trade","total_exposure","daily_spend","per_counterparty")}
                limits[currency]=values
                return 200,self.daemon.risk.configure(limits,max_pending=body.get("max_pending",2),expected_revision=body["expected_revision"])
            if path == "/v1/payment-coordination/free-execute":
                return 200,self.free_execute(body["offer_id"])
            if path in {"/v1/payment-coordination/refund", "/v1/payment-coordination/refund-reconcile"}:
                return 200,self.refund(body,reconcile=path.endswith("refund-reconcile"))
            prefix = "/v1/payment-coordination/plans/"
            if path.startswith(prefix):
                plan_id, _, action = path.removeprefix(prefix).rpartition("/")
                if action == "accept":
                    return 200, self.activate(plan_id, body.get("command_id") or plan_id)
                if action in {"pay", "reconcile"}:
                    if action == "reconcile" and self._row(plan_id)["record"]["terms"]["method"]=="x402/2":
                        return 200,self.reconcile_x402(plan_id)
                    return 200, self.pay(plan_id, reconcile=action == "reconcile")
                if action == "execute":
                    return 200, self.execute(plan_id)
                if action == "cancel":
                    row = self._row(plan_id)
                    result = self.payments.cancel(row["intent_id"])
                    record = self.store.get("payment_revocations",plan_id)
                    if not record:
                        record = signed({"v":"a2n-payment-revocation/1","author_did":self.daemon.identity.did,
                            "plan_id":plan_id,"reason":"CANCELED_BEFORE_PAYMENT_EXPOSURE"},signer_for(self.daemon.identity))
                        self.store.put("payment_revocations",plan_id,record)
                    ack = self._remote(row["base"],"revoke",{"record":record})
                    self.coordinator.verify(ack,"a2n-payment-revocation-ack/1",row["record"]["offer"]["provider_did"])
                    return 200,result
            raise ValueError("UNKNOWN_PAYMENT_COMMAND")
