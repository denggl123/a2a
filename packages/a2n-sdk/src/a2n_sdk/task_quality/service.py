"""Private task review lifecycle. Artifacts and calibration are injected ports."""
from __future__ import annotations
import math
import time
from .contracts import REVIEW_VERSION, clone, context, digest, identifier
from .rubrics import BUILTINS, combine, evaluate_checks, reference, validate_checks, validate_rubric
from .calibration import predict
from ..archives import archive, get_record

class TaskQualityService:
    def __init__(self, store, artifacts, *, calibration=None, reviewer=None, now=None):
        self.store,self.artifacts,self.calibration,self.now=store,artifacts,calibration,now or time.time
        self.reviewer=reviewer
        for value in BUILTINS: self.register(value)

    def register(self, definition):
        row=validate_rubric(definition); ref=reference(row)
        with self.store.tx():
            old=self.store.get("task_rubrics",ref)
            if old and digest(old)!=digest(row): raise ValueError("TASK_RUBRIC_VERSION_IMMUTABLE")
            if not old: self.store.put("task_rubrics",ref,row)
        return {"reference":ref,"digest":digest(row),"definition":clone(row)}

    def rubric(self, ref):
        if not isinstance(ref,str) or ref.count("@")!=1: raise ValueError("INVALID_TASK_RUBRIC_REFERENCE")
        for part in ref.split("@"): identifier(part)
        value=self.store.get("task_rubrics",ref)
        if value is None: raise ValueError("TASK_RUBRIC_NOT_FOUND")
        return value

    def plan(self, body):
        allowed={"scope","task_id","rubric","task_class","skill","workload_bucket","checks","origin"}
        if not isinstance(body,dict) or set(body)-allowed or not {"scope","task_id","rubric","skill"}<=set(body):
            raise ValueError("INVALID_TASK_REVIEW_PLAN")
        for key in ("scope","task_id","skill"): identifier(body[key])
        rubric=self.rubric(body["rubric"])
        ctx=context({"rubric":body["rubric"],"task_class":body.get("task_class",rubric["task_class"]),
                     "skill":body["skill"],"workload_bucket":body.get("workload_bucket","auto")})
        origin=body.get("origin","PRODUCTION")
        if origin not in {"PRODUCTION","CONTROLLED"}: raise ValueError("INVALID_TASK_REVIEW_ORIGIN")
        checks=validate_checks(body.get("checks",{}),rubric)
        row={"scope":body["scope"],"task_id":body["task_id"],"context":ctx,"checks":checks,"origin":origin}
        key="qr_"+digest([body["scope"],body["task_id"],body["rubric"],ctx["task_class"]])[:32]
        fingerprint=digest(row)
        with self.store.tx():
            old=get_record(self.store,"task_review_plans",key)
            if old:
                if old["fingerprint"]!=fingerprint: raise ValueError("IDEMPOTENCY_CONFLICT")
                return self.get(key)
            plans=self.store.items("task_review_plans")
            if len(plans)>=5000:
                archive(self.store,"task_review_plans",keep=4900,eligible=lambda r:r["state"]!="WAITING")
                plans=self.store.items("task_review_plans")
            if len(plans)>=5000 or sum(p["state"]=="WAITING" for p in plans.values())>=100:
                raise ValueError("TASK_REVIEW_PLAN_LIMIT_REACHED")
            description=self.artifacts.describe(body["scope"],body["task_id"])
            row.update({"plan_id":key,"fingerprint":fingerprint,"rubric_digest":digest(rubric),
                "subject":description["subject"],"planned":not description["already_admitted"],
                "created_at":self.now(),"state":"WAITING"})
            self.store.put("task_review_plans",key,row)
        return self.get(key)

    def _evaluation(self, plan, artifact):
        rubric=self.rubric(plan["context"]["rubric"])
        if (artifact["subject"]!=plan["subject"] or artifact["skill"]!=plan["context"]["skill"]
                or plan["context"]["workload_bucket"] not in {"auto",artifact["workload_bucket"]}):
            return None
        objective,reasons=evaluate_checks(artifact["result"],plan["checks"],self.reviewer,artifact=artifact)
        score=combine(rubric,objective,{})
        data={"v":REVIEW_VERSION,"plan_id":plan["plan_id"],"scope":plan["scope"],"task_id":plan["task_id"],
            "context":{**plan["context"],"workload_bucket":artifact["workload_bucket"]},**artifact["subject"],"version":artifact["version"],"trade_uid":artifact["trade_uid"],
            "at":artifact["at"],"known_self":artifact["known_self"],"origin":plan["origin"],"planned":plan["planned"],
            "rubric_digest":plan["rubric_digest"],"artifact_digest":artifact["result_digest"],"objective":objective,
            "human":{},"revision":0,"label_at":self.now(),"consent":False,"withdrawn":False,"score":score,
            "reasons":reasons+([] if plan["planned"] else ["RETROSPECTIVE_REVIEW_LIMITED"])}
        data["record_digest"]=digest(data)
        return data

    def drain(self, limit=10):
        pending=[p for p in self.store.items("task_review_plans").values() if p["state"]=="WAITING"]
        pending.sort(key=lambda p:(p.get("last_checked",0),p["created_at"],p["plan_id"]))
        completed=0
        for p in pending[:limit]:
            artifact=self.artifacts.delivered(p["scope"],p["task_id"])
            row=self._evaluation(p,artifact) if artifact is not None else None
            with self.store.tx():
                current=self.store.get("task_review_plans",p["plan_id"])
                if current["state"]!="WAITING": continue
                current={**current,"last_checked":self.now()}
                if artifact is not None:
                    if row is not None:
                        self.store.put("task_reviews",p["plan_id"],row)
                        self.store.put("task_review_versions",digest([p["plan_id"],0]),row)
                        current["state"]="REVIEWED"; completed+=1
                    else:
                        current["state"]="CONTEXT_MISMATCH"
                self.store.put("task_review_plans",p["plan_id"],current)
        return {"completed":completed,"pending":len(pending)-completed}

    def get(self, plan_id):
        plan=get_record(self.store,"task_review_plans",identifier(plan_id))
        if plan is None: raise ValueError("TASK_REVIEW_PLAN_NOT_FOUND")
        review=self.store.get("task_reviews",plan_id)
        return clone({"plan":plan,"review":review})

    def review(self, plan_id, body):
        required={"scores","consent","withdrawn","expected_revision"}
        if not isinstance(body,dict) or not required<=set(body) or set(body)-required-{"label_source"}:
            raise ValueError("INVALID_TASK_HUMAN_REVIEW")
        if type(body["expected_revision"]) is not int or type(body["consent"]) is not bool or type(body["withdrawn"]) is not bool:
            raise ValueError("INVALID_TASK_HUMAN_REVIEW_VERSION")
        with self.store.tx():
            value=self.get(plan_id); old=value["review"]
            if old is None: raise ValueError("DELIVERED_TASK_REVIEW_REQUIRED")
            if old["revision"]!=body["expected_revision"]: raise ValueError("REV_CONFLICT")
            label_source=body.get("label_source",old.get("label_source","HUMAN"))
            if not isinstance(label_source,str) or label_source not in {"HUMAN","ASSISTANT"}:
                raise ValueError("INVALID_TASK_REVIEW_LABEL_SOURCE")
            rubric=self.rubric(old["context"]["rubric"])
            scores=body["scores"]
            if not isinstance(scores,dict) or set(scores)-set(rubric["dimensions"]): raise ValueError("INVALID_TASK_HUMAN_DIMENSIONS")
            for k,v in scores.items():
                if type(v) is not int or not 1<=v<=5 or rubric["dimensions"][k]["sources"].get("human",0)<=0:
                    raise ValueError("INVALID_TASK_HUMAN_SCORE")
            row={**old,"human":clone(scores),"consent":body["consent"],"withdrawn":body["withdrawn"],
                 "label_source":label_source,"revision":old["revision"]+1,"label_at":self.now(),"score":combine(rubric,old["objective"],scores)}
            row.pop("record_digest",None);row["record_digest"]=digest(row)
            self.store.put("task_reviews",plan_id,row)
            self.store.put("task_review_versions",digest([plan_id,row["revision"]]),row)
        return self.get(plan_id)

    def recheck(self,plan_id,body):
        if not isinstance(body,dict) or set(body)!={'expected_revision'} or type(body['expected_revision']) is not int:
            raise ValueError('INVALID_TASK_RECHECK_VERSION')
        value=self.get(plan_id);previous=value['review']
        if previous is None:raise ValueError('DELIVERED_TASK_REVIEW_REQUIRED')
        if previous['revision']!=body['expected_revision']:raise ValueError('REV_CONFLICT')
        artifact=self.artifacts.delivered(value['plan']['scope'],value['plan']['task_id'])
        fresh=self._evaluation(value['plan'],artifact) if artifact else None
        if fresh is None:raise ValueError('VERIFIED_ORIGINAL_ARTIFACT_REQUIRED')
        with self.store.tx():
            old=self.get(plan_id)['review']
            if old['revision']!=body['expected_revision']:raise ValueError('REV_CONFLICT')
            for dimension,metric in old['objective'].items():
                if metric.get('value') is not None and fresh['objective'].get(dimension,{}).get('value') is None:
                    fresh['objective'][dimension]=metric
                    fresh['reasons'].append('RECHECK_UNAVAILABLE_PREVIOUS_MEASUREMENT_RETAINED')
            for key in ('human','consent','withdrawn','label_source','label_at'):
                if key in old:fresh[key]=old[key]
            fresh.update(revision=old['revision']+1,rechecked_at=self.now(),score=combine(self.rubric(old['context']['rubric']),fresh['objective'],old['human']))
            fresh.pop('record_digest',None);fresh['record_digest']=digest(fresh)
            self.store.put('task_reviews',plan_id,fresh)
            self.store.put('task_review_versions',digest([plan_id,fresh['revision']]),fresh)
        return self.get(plan_id)

    def observations(self, task_context):
        c=context(task_context);rubric=self.rubric(c["rubric"]);target=rubric["calibration_target"]
        rows=[]
        for r in self.store.items("task_reviews").values():
            if r["context"]!=c: continue
            rows.append({k:r[k] for k in ("trade_uid","provider_did","at","label_at","revision","record_digest",
                                          "origin","planned","known_self","consent","withdrawn")} | {
                "prediction":r["score"]["objective_prediction"],"objective_coverage":r["score"]["objective_coverage"],
                "label_source":r.get("label_source","HUMAN"),
                "label":(r["human"][target]-1)/4 if target in r["human"] else None,"workload_bucket":c["workload_bucket"]})
        return {"rows":rows,"rubric_digest":digest(rubric)}

    def metric(self, provider_did, service_id, version, task, now):
        rubric=self.rubric(task["rubric"])
        c=context({"rubric":task["rubric"],"task_class":task.get("task_class",rubric["task_class"]),
                   "skill":task["skill"],"workload_bucket":task.get("workload_bucket","small")})
        model=self.calibration.active(c,now) if self.calibration else {"state":"DEFAULT","revision":0,"model_id":None}
        selected={}
        for r in self.store.items("task_reviews").values():
            if (r["provider_did"]!=provider_did or r["service_id"]!=service_id or r["version"]!=version or r["context"]!=c
                    or r["withdrawn"] or r["known_self"] or not now-90*86400<=r["at"]<=now): continue
            old=selected.get(r["trade_uid"])
            if old is None or r["revision"]>old["revision"]: selected[r["trade_uid"]]=r
        mass=weighted=coverage=0.0;refs=[];limited=False;sources={}
        for r in sorted(selected.values(),key=lambda r:(r["at"],r["trade_uid"]))[-100:]:
            score=combine(rubric,r["objective"],r["human"],objective_map=(lambda x:predict(model["knots"],x)) if model["state"]=="ACTIVE" else None)
            if score["value"] is None: continue
            weight=math.exp(-math.log(2)*(now-r["at"])/(90*86400))
            mass+=weight;weighted+=weight*score["value"];coverage+=weight*score["coverage"]
            source=r.get("label_source","HUMAN");sources[source]=sources.get(source,0)+1
            refs.append(r["record_digest"]);limited|=not r["planned"] or r["origin"]!="PRODUCTION" or source=="ASSISTANT"
        mean_coverage=coverage/mass if mass else 0
        return {"value":(2.5+weighted)/(5+mass) if mass else None,
                "status":"SUPPORTED" if mass>=5 and mean_coverage>=.8 and not limited else "LIMITED" if mass else "UNKNOWN",
                "support":{"samples":len(refs),"effective_mass":mass,"coverage":mean_coverage,"local_only":True,"label_sources":sources},
                "raw":{"rubric":task["rubric"],"rubric_digest":digest(rubric),"task_class":c["task_class"],
                       "scope":"task_rubric_version_skill_workload","calibration":{k:v for k,v in model.items() if k!="knots"}},
                "reasons":["TASK_SPECIFIC_PRIVATE_REVIEW"] if mass else ["TASK_SPECIFIC_REVIEW_UNKNOWN"],
                "refs":sorted(set(refs))[:64],"ref_count":len(set(refs)),"refs_truncated":len(set(refs))>64,
                "valid_until":min(now+30,model.get("valid_until",now+30))}

    def command(self,path,body):
        if path=="/v1/task-quality/rubrics": return 201,self.register(body)
        if path=="/v1/task-quality/plans": return 201,self.plan(body)
        if path.startswith("/v1/task-quality/plans/"):
            key=path.split("/")[-2]
            if path.endswith("/review"): return 200,self.review(key,body)
            if path.endswith('/recheck'):return 200,self.recheck(key,body)
            if path.endswith("/cancel"):
                with self.store.tx():
                    row=self.get(key)["plan"]
                    if row["state"]!="WAITING": raise ValueError("TASK_REVIEW_STATE_CONFLICT")
                    self.store.put("task_review_plans",key,{**row,"state":"CANCELLED"})
                return 200,self.get(key)
        raise ValueError("UNKNOWN_TASK_QUALITY_COMMAND")

    def read(self,path,query):
        if path=="/v1/task-quality/rubrics":
            return 200,{"items":[self.register(r) for r in self.store.items("task_rubrics").values()]}
        if path=="/v1/task-quality/plans":
            from ..archives import page_records
            page=page_records(self.store,'task_review_plans',query)
            keys=page.pop('keys');return 200,{**page,'items':[self.get(k) for k in keys]}
        if path.startswith("/v1/task-quality/plans/"): return 200,self.get(path.rsplit("/",1)[-1])
        raise ValueError("UNKNOWN_TASK_QUALITY_QUERY")
