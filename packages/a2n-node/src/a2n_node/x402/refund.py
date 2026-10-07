"""Explicit reverse EIP-3009 transfer; no resource invocation or escrow claim."""
from a2n_sdk.trade_facts import digest
from a2n_sdk.x402.protocol import validate_settlement
from .http import HTTPFacilitator


class X402RefundDriver:
    def __init__(self, store, source, facilitator_url):
        self.store,self.source=store,source
        self.facilitator=HTTPFacilitator(facilitator_url,source.http)
        self.driver_id="eip3009-refund/1:"+digest([source.driver_id,facilitator_url])
        for key,row in store.items("x402_refund_attempts").items():
            if row["driver_id"]==self.driver_id and row["state"]=="PREPARING":
                store.put("x402_refund_attempts",key,{**row,"state":"NOT_EXPOSED"})

    def capabilities(self):
        return {"driver_id":self.driver_id,"currencies":self.source.capabilities()["currencies"],
            "method":"eip3009-refund/1","refund":True,"platform_commission_minor":0}

    def _result(self,intent,result):
        return {**result,"driver_id":self.driver_id,"verified_source":True,
            "currency":intent["currency"],"amount_minor":intent["amount_minor"],"payee":intent["payee"]}

    def submit(self,intent):
        key=intent["intent_id"]
        with self.store.tx():
            previous=self.store.get("x402_refund_attempts",key)
            if not previous:
                self.store.put("x402_refund_attempts",key,{"state":"PREPARING","driver_id":self.driver_id})
        if previous:
            return self.query(intent)
        try:
            plan=self.store.get("x402_refund_plans",key)
            if not plan or not self.source.signer or self.source.currency_for(plan["accepted"])!=intent["currency"]:
                raise ValueError("X402_REFUND_PLAN_MISSING")
            if plan["accepted"]["payTo"]!=intent["payee"] or int(plan["accepted"]["amount"])!=intent["amount_minor"]:
                raise ValueError("X402_REFUND_TERMS_MISMATCH")
            payload=self.source.signer.sign(plan["required"],plan["accepted"])
            anchor=self.source.anchor(payload)
            with self.store.tx():
                self.store.put("x402_refund_authorizations",key,{"payload":payload,"anchor":anchor})
                self.store.put("x402_refund_attempts",key,{"state":"EXPOSED","driver_id":self.driver_id})
        except Exception:
            self.store.put("x402_refund_attempts",key,{"state":"NOT_EXPOSED","driver_id":self.driver_id})
            return self._result(intent,{"state":"FAILED","definitive":True,"reason":"REFUND_AUTHORIZATION_NOT_EXPOSED"})
        try:
            verification=self.facilitator.verify(payload,plan["accepted"])
            if verification.get("isValid") is True:
                hint=validate_settlement(self.facilitator.settle(payload,plan["accepted"]),payload)
                self.store.put("x402_refund_hints",key,hint)
        except Exception:
            pass
        return self.query(intent)

    def query(self,intent):
        key=intent["intent_id"]
        row=self.store.get("x402_refund_authorizations",key)
        if not row:
            attempt=self.store.get("x402_refund_attempts",key)
            return self._result(intent,{"state":"FAILED" if attempt and attempt["state"]=="NOT_EXPOSED" else "UNKNOWN",
                "definitive":True,"reason":"REFUND_AUTHORIZATION_NOT_EXPOSED"})
        result=self.source.observe(row["payload"],row["anchor"],self.store.get("x402_refund_hints",key))
        if result["state"]=="CONFIRMED":
            result={**result,"reference":result["settlement"]["transaction"],"fee_minor":0}
        return self._result(intent,result)

    def refund(self,intent):
        raise ValueError("REFUND_ORDERS_ARE_NOT_RECURSIVELY_REFUNDED")
