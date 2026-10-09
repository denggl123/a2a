"""Optional independent evaluators injected into task quality through a port."""
from __future__ import annotations
import json
import re
from a2n_sdk.trade_facts import digest
from .container_engine import run_json,image_id
from .x402.http import HTTPClient

PYTHON_DRIVER = '''import json,sys,contextlib,io
j=json.load(sys.stdin);ns={};records=[]
with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
 try:
  exec(compile(j["code"],"<delivered-code>","exec"),ns)
  fn=ns[j["function"]]
  for args in j["args"]:
   try:records.append({"ok":True,"value":fn(*args)})
   except Exception:records.append({"ok":False})
 except Exception:records=[{"ok":False}]*len(j["args"])
print(json.dumps({"results":records},allow_nan=False))
'''

class QualityReviewers:
    def __init__(self,store,assets=None):self.store,self.assets=store,assets

    def settings(self):
        cfg=dict(self.store.get("reviewer_settings","current",{}));cfg.pop('reviewer_digest',None)
        cfg['reviewer_digest']=digest({'evaluator_version':2,'settings':{k:v for k,v in cfg.items() if k!='token'},
                                       'media':self.store.get('media_settings','current',{})})
        return cfg

    def status(self):
        cfg=self.settings()
        return {k:v for k,v in cfg.items() if k!="token"}

    def configure(self,body):
        allowed={"python_image","semantic_url","token","allow_http","grant_execution","grant_external_review"}
        if not isinstance(body,dict) or set(body)-allowed:raise ValueError("INVALID_REVIEWER_SETTINGS")
        cfg={}
        if body.get("python_image"):
            if body.get("grant_execution") is not True:raise ValueError("REVIEWER_EXECUTION_GRANT_REQUIRED")
            cfg["python_image"]=image_id(body["python_image"])
        if body.get("semantic_url"):
            if body.get("grant_external_review") is not True:raise ValueError("REVIEWER_EXTERNAL_GRANT_REQUIRED")
            client=HTTPClient(allow_http=body.get("allow_http",False));client.validate_url(body["semantic_url"])
            cfg.update({k:body[k] for k in ("semantic_url","token","allow_http") if k in body})
        cfg["reviewer_digest"]=digest({k:v for k,v in cfg.items() if k!="token"})
        self.store.put("reviewer_settings","current",cfg);return self.status()

    def evaluate(self,value,spec,artifact=None):
        cfg=self.settings()
        if not cfg or spec.get("reviewer_digest")!=cfg.get("reviewer_digest"):
            return {"score":None,"state":"REVIEWER_NOT_GRANTED_OR_CHANGED"}
        try:
            if spec['kind']=='media-constraints':
                if self.assets is None or not artifact:return {'score':None,'state':'VERIFIED_MEDIA_ARTIFACT_REQUIRED'}
                metadata=self.assets.media.inspect(value,artifact['trade_uid'])
                from a2n_sdk.task_quality.rubrics import evaluate_checks
                result,_=evaluate_checks(metadata,{'format':spec['constraints']})
                score=result['format']['value'];source='INDEPENDENT_PRIVATE_ARTIFACT_INSPECTION'
            elif spec["kind"]=="python-function":
                if not cfg.get("python_image") or not isinstance(value,str) or len(value)>65536:return {"score":None,"state":"CODE_REVIEW_UNAVAILABLE"}
                result=run_json(cfg["python_image"],["python","-I","-S","-c",PYTHON_DRIVER],
                                {"code":value,"function":spec["function"],"args":[case['args'] for case in spec['cases']]},timeout_ms=5000)
                actual=result["results"]
                if not isinstance(actual,list) or len(actual)!=len(spec["cases"]) or any(not isinstance(x,dict) or type(x.get('ok')) is not bool for x in actual):raise ValueError("INVALID_INDEPENDENT_TEST_RESULT")
                passed=[item['ok'] and digest(item.get('value'))==digest(case['expected']) for item,case in zip(actual,spec['cases'])]
                score=sum(passed)/len(passed);source="ISOLATED_INDEPENDENT_TESTS"
            elif spec["kind"]=="semantic":
                if not cfg.get("semantic_url"):return {"score":None,"state":"SEMANTIC_REVIEW_UNAVAILABLE"}
                from urllib.request import Request,build_opener,ProxyHandler,HTTPRedirectHandler
                class NoRedirect(HTTPRedirectHandler):
                    def redirect_request(self,req,fp,code,msg,headers,newurl):return None
                client=HTTPClient(allow_http=cfg.get("allow_http",False));client.validate_url(cfg["semantic_url"])
                request={"v":"a2n-quality-evaluator/1","result":value,"requirements":spec["requirements"]}
                raw=json.dumps(request,ensure_ascii=False,allow_nan=False).encode()
                headers={"Content-Type":"application/json"}
                if cfg.get("token"):headers["Authorization"]="Bearer "+cfg["token"]
                with build_opener(ProxyHandler({}),NoRedirect()).open(Request(cfg["semantic_url"],raw,headers),timeout=10) as response:
                    raw=response.read(65537)
                if len(raw)>65536:raise ValueError("SEMANTIC_REVIEW_OUTPUT_LIMIT")
                result=json.loads(raw);score=result.get("score");source="OWNER_CONFIGURED_SEMANTIC_REVIEWER"
                if type(score) not in (int,float) or not 0<=score<=1:raise ValueError("INVALID_SEMANTIC_REVIEW_SCORE")
            else:raise ValueError("INVALID_REVIEWER_KIND")
            return {"score":score,"state":"REVIEWED","source":source,"evidence_digest":digest([value,spec,result])}
        except (ValueError,OSError,KeyError,TypeError,TimeoutError):
            return {"score":None,"state":"INDEPENDENT_REVIEW_UNAVAILABLE"}
