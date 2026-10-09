"""Only this adapter knows projection, trade and encrypted call artifact schemas."""
from __future__ import annotations
from .trade_facts import digest

class TaskArtifactAdapter:
    def __init__(self,store,*,node_did,runtime=None): self.store,self.node_did,self.runtime=store,node_did,runtime

    def describe(self,scope,task_id):
        projection=self.store.get("projections",scope)
        if not projection and self.runtime is not None:
            item=self.runtime.imported.get(scope)
            projection={"network_card":item.network_card} if item else None
        if not projection: raise ValueError("BUYER_PROJECTION_REQUIRED")
        extension=projection.get("network_card",{}).get("x-a2n",{}).get("projection",{})
        provider,sid=extension.get("node_did"),extension.get("service_id")
        if not provider or not sid: raise ValueError("VERIFIED_PROVIDER_DECLARATION_REQUIRED")
        return {"subject":{"provider_did":provider,"service_id":sid},
                "already_admitted":bool(self.store.get("trade_fact_index",digest([scope,task_id])) or self.store.task(scope,task_id))}

    def delivered(self,scope,task_id):
        uid=self.store.get("trade_fact_index",digest([scope,task_id]));fact=self.store.get("trade_facts",uid) if uid else None
        if not fact or fact["execution"]!="DELIVERED": return None
        if not fact.get("relation_verified") or fact.get("buyer_did")!=self.node_did:
            raise ValueError("VERIFIED_BUYER_OWNED_DELIVERY_REQUIRED")
        call=self.store.task(scope,task_id)
        if not call or not call.get("outcome") or call["outcome"].get("result") is None: return None
        request=call.get("request") or {};result=call["outcome"]["result"]
        if digest(result)!=fact.get("delivery_digest"): raise ValueError("DELIVERY_ARTIFACT_DIGEST_MISMATCH")
        observations=[r for r in self.store.items("execution_observations").values() if r["scope"]==scope and r["task_id"]==task_id]
        observation=observations[0] if observations else {}
        return {"subject":{"provider_did":fact["provider_did"],"service_id":fact["service_id"]},
                "trade_uid":uid,"at":fact["delivered_at"],"known_self":fact.get("known_self",False),
                "version":fact.get("version",""),"skill":request.get("skill",""),
                "workload_bucket":observation.get("workload_bucket","unknown"),"result":result,"result_digest":digest(result)}
