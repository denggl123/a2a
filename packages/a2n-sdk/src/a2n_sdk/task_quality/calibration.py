"""Conservative empirical utility calibration. Pure calculations and injected data."""
from __future__ import annotations
import math
import secrets
import time
from .contracts import CALIBRATION_VERSION, clone, context, digest, identifier, number

MIN_SAMPLES, MIN_PROVIDERS, MIN_DAYS = 40, 3, 3

def _weights(rows):
    groups = {}
    for row in rows: groups[row["provider_did"]] = groups.get(row["provider_did"], 0)+1
    return [1/groups[r["provider_did"]] for r in rows]

def _isotonic(rows):
    groups = {}
    for row, weight in zip(rows, _weights(rows)):
        item = groups.setdefault(row["prediction"], [0.0, 0.0])
        item[0] += weight; item[1] += weight*row["label"]
    blocks = []
    for x, (weight, total) in sorted(groups.items()):
        blocks.append({"xs": [x], "weight": weight, "total": total})
        while len(blocks) >= 2 and blocks[-2]["total"]/blocks[-2]["weight"] > blocks[-1]["total"]/blocks[-1]["weight"]:
            b, a = blocks.pop(), blocks.pop()
            blocks.append({"xs": a["xs"]+b["xs"], "weight": a["weight"]+b["weight"], "total": a["total"]+b["total"]})
    return [[x, block["total"]/block["weight"]] for block in blocks for x in block["xs"]]

def predict(knots, value):
    number(value)
    if not knots: raise ValueError("CALIBRATION_KNOTS_REQUIRED")
    if value <= knots[0][0]: return knots[0][1]
    for (x0, y0), (x1, y1) in zip(knots, knots[1:]):
        if value <= x1: return y0+(y1-y0)*(value-x0)/(x1-x0)
    return knots[-1][1]

def _errors(rows, function):
    weights = _weights(rows); total = math.fsum(weights)
    return {"mse": math.fsum(w*(function(r["prediction"])-r["label"])**2 for r,w in zip(rows,weights))/total,
            "mae": math.fsum(w*abs(function(r["prediction"])-r["label"]) for r,w in zip(rows,weights))/total}

def fit(observations, *, now):
    """Temporal holdout, balanced providers, deduplication; no in-sample success claim."""
    latest, excluded = {}, 0
    for row in observations:
        if (row.get("origin") != "PRODUCTION" or not row.get("consent") or not row.get("planned")
                or row.get("known_self") or row.get("withdrawn") or row.get("objective_coverage", 0) < .8
                or row.get("label_source","HUMAN") not in {"HUMAN","ASSISTANT"}):
            excluded += 1; continue
        try:
            number(row["prediction"]); number(row["label"])
            number(row["at"], maximum=now); number(row["label_at"], maximum=now)
            if row["label_at"] < row["at"] or row["at"] < now-90*86400:
                excluded += 1; continue
            identifier(row["workload_bucket"])
            if not isinstance(row["provider_did"], str) or not row["provider_did"] or not row.get("trade_uid"):
                raise ValueError("INVALID_CALIBRATION_RELATION")
        except (KeyError, ValueError, TypeError):
            excluded += 1; continue
        old = latest.get(row["trade_uid"])
        if old is None or (row["revision"], row["record_digest"]) > (old["revision"], old["record_digest"]):
            latest[row["trade_uid"]] = row
    # Bound one provider's influence and computation, while preserving a chronological split.
    selected, counts = [], {}
    for row in sorted(latest.values(), key=lambda r:(r["label_at"],r["trade_uid"]), reverse=True):
        provider = row["provider_did"]
        if counts.get(provider,0) >= 100: excluded += 1; continue
        counts[provider] = counts.get(provider,0)+1
        selected.append(row)
        if len(selected) == 1000: break
    rows = sorted(selected, key=lambda r:(r["label_at"],r["trade_uid"]))
    providers = len({r["provider_did"] for r in rows})
    days = len({int(r["at"]//86400) for r in rows})
    sources={}
    for row in rows:
        source=row.get("label_source","HUMAN");sources[source]=sources.get(source,0)+1
    support = {"samples":len(rows), "providers":providers, "days":days, "excluded":excluded,
               "label_sources":sources,
               "minimum_samples":MIN_SAMPLES, "minimum_providers":MIN_PROVIDERS, "minimum_days":MIN_DAYS,
               "independent_people_verified":False}
    base = {"v":CALIBRATION_VERSION, "support":support,
            "data_digest":digest([[r["trade_uid"],r["record_digest"]] for r in rows]),
            "references":{r["trade_uid"]:r["record_digest"] for r in rows}, "knots":[], "reasons":[],
            "notice":"映射表示本机任务检查与本人使用体验的关系，不是客观正确概率。"}
    if len(rows) < MIN_SAMPLES or providers < MIN_PROVIDERS or days < MIN_DAYS:
        return {**base,"state":"INSUFFICIENT_DATA","reasons":["REAL_LABELLED_HISTORY_REQUIRED"]}
    if len(sources)>1:
        return {**base,"state":"INSUFFICIENT_DIVERSITY","reasons":["EVALUATION_SOURCES_REQUIRE_SEPARATE_CONTEXTS"]}
    if max(r["label"] for r in rows)-min(r["label"] for r in rows) < .25:
        return {**base,"state":"INSUFFICIENT_VARIATION","reasons":["MORE_DISTINCT_USE_EXPERIENCES_REQUIRED"]}
    cut = len(rows)-max(8, math.ceil(len(rows)*.2))
    train, holdout = rows[:cut], rows[cut:]
    if len({r["provider_did"] for r in train}) < 3 or len({r["provider_did"] for r in holdout}) < 2:
        return {**base,"state":"INSUFFICIENT_DIVERSITY","reasons":["TEMPORAL_HOLDOUT_PROVIDER_DIVERSITY_REQUIRED"]}
    knots = _isotonic(train)
    baseline, measured = _errors(holdout, lambda x:x), _errors(holdout, lambda x:predict(knots,x))
    validation = {"method":"CHRONOLOGICAL_HOLDOUT_PROVIDER_BALANCED", "training_samples":len(train),
                  "holdout_samples":len(holdout), "baseline":baseline, "calibrated":measured,
                  "training_cutoff":train[-1]["label_at"], "refit_on_holdout":False}
    acceptable = (baseline["mse"]-measured["mse"] >= max(.000001,baseline["mse"]*.05)
                  and measured["mae"] <= baseline["mae"])
    # Training knots remain unchanged after validation, so the evaluated model is the installed model.
    return {**base,"state":"VALIDATED" if acceptable else "NO_VALIDATED_IMPROVEMENT", "knots":knots,
            "validation":validation,"reasons":[] if acceptable else ["DEFAULT_MAPPING_RETAINED"]}

class CalibrationService:
    def __init__(self, store, source, *, now=None):
        self.store, self.source, self.now = store, source, now or time.time

    def status(self, task_context):
        c = context(task_context); data = self.source.observations(c)
        eligible = [r for r in data["rows"] if r["origin"] == "PRODUCTION" and r["consent"] and r["planned"]
                    and not r["known_self"] and not r["withdrawn"] and r["prediction"] is not None and r["label"] is not None
                    and r.get("label_source","HUMAN") in {"HUMAN","ASSISTANT"}
                    and r["objective_coverage"]>=.8 and self.now()-90*86400<=r["at"]<=r["label_at"]<=self.now()]
        sources={}
        for row in eligible:
            source=row.get("label_source","HUMAN");sources[source]=sources.get(source,0)+1
        return {"context":c,"v":CALIBRATION_VERSION,"available_reviews":len(data["rows"]),
                "consented_production_reviews":len(eligible),"controlled_reviews":sum(r["origin"]=="CONTROLLED" for r in data["rows"]),
                "qualified_providers":len({r['provider_did'] for r in eligible}),
                "qualified_days":len({int(r['at']//86400) for r in eligible}),"label_sources":sources,
                "minimum_samples":MIN_SAMPLES,"minimum_providers":MIN_PROVIDERS,"minimum_days":MIN_DAYS,
                "active":self.active(c,self.now())}

    def create(self, body):
        if not isinstance(body,dict) or set(body) != {"context","command_id"}:
            raise ValueError("INVALID_CALIBRATION_REQUEST")
        c = context(body["context"]); command = identifier(body["command_id"])
        data = self.source.observations(c); result = fit(data["rows"],now=self.now())
        fingerprint = digest([c,result["data_digest"]])
        with self.store.tx():
            previous = self.store.get("calibration_commands",command)
            if previous:
                if previous["fingerprint"] != fingerprint: raise ValueError("IDEMPOTENCY_CONFLICT")
                return self.get(previous["model_id"])
            if len(self.store.items("calibration_models")) >= 128:
                from ..archives import archive
                active={r["model_id"] for r in self.store.items("calibration_active").values() if r.get("model_id")}
                archive(self.store,"calibration_models",keep=120,eligible=lambda r:r["model_id"] not in active)
            if len(self.store.items("calibration_models")) >= 128: raise ValueError("CALIBRATION_MODEL_LIMIT_REACHED")
            mid = "cm_"+secrets.token_hex(16)
            row = {**result,"model_id":mid,"context":c,"rubric_digest":data["rubric_digest"],
                   "created_at":self.now(),"valid_until":self.now()+30*86400}
            self.store.put("calibration_models",mid,row)
            self.store.put("calibration_commands",command,{"fingerprint":fingerprint,"model_id":mid})
        return self.get(mid)

    def get(self, model_id):
        from ..archives import get_record
        row = get_record(self.store,"calibration_models",identifier(model_id))
        if row is None: raise ValueError("CALIBRATION_MODEL_NOT_FOUND")
        # IDs and digests suffice for validity checks; never expose training task contents.
        return clone({k:v for k,v in row.items() if k != "references"})

    def _valid(self, row, now):
        if row["state"] != "VALIDATED" or row["valid_until"] <= now: return False
        data = self.source.observations(row["context"])
        current = {r["trade_uid"]:r["record_digest"] for r in data["rows"] if r["consent"] and not r["withdrawn"]
                   and r["origin"]=="PRODUCTION" and r["planned"] and not r["known_self"]
                   and r["objective_coverage"]>=.8 and r["prediction"] is not None and r["label"] is not None
                   and r.get("label_source","HUMAN") in {"HUMAN","ASSISTANT"}
                   and now-90*86400<=r["at"]<=r["label_at"]<=now}
        return data["rubric_digest"]==row["rubric_digest"] and all(current.get(k)==v for k,v in row["references"].items())

    def active(self, task_context, now):
        from ..archives import get_record
        c = context(task_context)
        row = self.store.get("calibration_active",digest(c),{"revision":0,"model_id":None})
        model = get_record(self.store,"calibration_models",row["model_id"]) if row["model_id"] else None
        if not model: return {**row,"state":"DEFAULT"}
        if not self._valid(model,now): return {**row,"state":"INVALIDATED","knots":[]}
        return {**row,"state":"ACTIVE","knots":model["knots"],"valid_until":model["valid_until"]}

    def activate(self, body):
        if not isinstance(body,dict) or set(body) != {"context","model_id","expected_revision"} or type(body["expected_revision"]) is not int:
            raise ValueError("INVALID_CALIBRATION_ACTIVATION")
        c=context(body["context"]); key=digest(c)
        with self.store.tx():
            old=self.store.get("calibration_active",key,{"revision":0,"model_id":None})
            if old["revision"] != body["expected_revision"]: raise ValueError("REV_CONFLICT")
            if body["model_id"] is not None:
                from ..archives import get_record
                model=get_record(self.store,"calibration_models",identifier(body["model_id"]))
                if not model or model["context"]!=c or not self._valid(model,self.now()):
                    raise ValueError("VALIDATED_CALIBRATION_REQUIRED")
            row={"context":c,"model_id":body["model_id"],"revision":old["revision"]+1,"updated_at":self.now()}
            self.store.put("calibration_active",key,row)
            self.store.put("calibration_active_versions",digest([c,row["revision"]]),row)
        return self.active(c,self.now())

    def command(self, path, body):
        if path == "/v1/calibration/fit": return 201,self.create(body)
        if path == "/v1/calibration/activate": return 200,self.activate(body)
        raise ValueError("UNKNOWN_CALIBRATION_COMMAND")

    def read(self, path, query):
        if path == "/v1/calibration/status": return 200,self.status(query)
        if path == "/v1/calibration/models":
            from ..archives import page_records
            page=page_records(self.store,'calibration_models',query)
            keys=page.pop('keys');return 200,{**page,'items':[self.get(k) for k in keys]}
        if path.startswith("/v1/calibration/models/"): return 200,self.get(path.rsplit("/",1)[-1])
        raise ValueError("UNKNOWN_CALIBRATION_QUERY")
