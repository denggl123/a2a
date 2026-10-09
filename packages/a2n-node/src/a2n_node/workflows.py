"""Signed container workflows use the existing binding, history and trade books."""
from __future__ import annotations
import re
import threading
from a2n_sdk.experience import unsigned
from a2n_sdk.trade_facts import digest
from .feedback_identity import verifier_for
from .container_engine import image_id,run_json

class WorkflowService:
    def __init__(self,store,runtime,assets=None):
        self.store,self.runtime=store,runtime;self.verify=verifier_for();self.slots=threading.BoundedSemaphore(2)
        self.assets=assets
        self.identity=None;self.job_slots=threading.BoundedSemaphore(2);self.job_lock=threading.Lock()
        self.mount_lock=threading.RLock()
        self.stop_event=threading.Event();self.threads=set()

    def retry(self,body):
        row=self.store.get('workflow_installs',body.get('job_id'))
        if not row:raise ValueError('WORKFLOW_INSTALL_NOT_FOUND')
        return self.submit({**row['request'],'retry':True})

    def sign(self,body):
        from a2n_sdk.experience import signed
        from .feedback_identity import signer_for
        if self.identity is None:raise ValueError('WORKFLOW_PUBLISHER_REQUIRED')
        manifest=dict(body['manifest'])
        if manifest.get('author_did') not in {None,self.identity.did} or 'proof' in manifest:raise ValueError('OWN_UNSIGNED_WORKFLOW_REQUIRED')
        manifest['author_did']=self.identity.did;manifest['v']='a2n-container-workflow/1'
        result=signed(manifest,signer_for(self.identity));self.preview(result);return result

    def submit(self,body):
        preview=self.preview(body.get('manifest'))
        if body.get('accepted_digest')!=preview['preview_digest'] or body.get('granted_permissions')!=preview['manifest']['permissions'] or body.get('download_dependencies') is not True:raise ValueError('EXPLICIT_WORKFLOW_DOWNLOAD_GRANT_REQUIRED')
        cid=body.get('command_id')
        if not isinstance(cid,str) or not 1<=len(cid)<=128:raise ValueError('WORKFLOW_COMMAND_ID_REQUIRED')
        jid='wi_'+digest(cid)[:32]
        with self.job_lock:
            if self.stop_event.is_set():raise ValueError('NODE_STOPPING')
            old=self.store.get('workflow_installs',jid)
            if old:
                if old['preview_digest']!=preview['preview_digest']:raise ValueError('IDEMPOTENCY_CONFLICT')
                if old['phase'] not in {'FAILED','RECOVERY_AVAILABLE'} or body.get('retry') is not True:return old
            if not self.job_slots.acquire(blocking=False):raise ValueError('WORKFLOW_INSTALL_CAPACITY_LIMIT')
            row={'job_id':jid,'preview_digest':preview['preview_digest'],'phase':'QUEUED','request':body}
            self.store.put('workflow_installs',jid,row)
        def worker():
            try:
                image=preview['manifest']['image']
                try:image_id(image)
                except ValueError:
                    if '@sha256:' not in image:raise ValueError('OFFLINE_IMAGE_NOT_INSTALLED')
                    import subprocess,os
                    self.store.put('workflow_installs',jid,{**row,'phase':'FETCHING_DEPENDENCY'})
                    kwargs={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
                    import time
                    process=subprocess.Popen(['docker','pull',image],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**kwargs)
                    deadline=time.monotonic()+240
                    while process.poll() is None:
                        if self.stop_event.wait(.2) or time.monotonic()>deadline:
                            process.kill();process.wait();raise ValueError('WORKFLOW_DEPENDENCY_INTERRUPTED')
                    if process.returncode:raise ValueError('WORKFLOW_DEPENDENCY_FAILED')
                if self.stop_event.is_set():return
                result=self.install(body)
                self.store.put('workflow_installs',jid,{**row,'phase':'READY','service_id':result['service_id']})
            except Exception as exc:
                if not self.stop_event.is_set():self.store.put('workflow_installs',jid,{**row,'phase':'FAILED','error_type':type(exc).__name__})
            finally:
                self.job_slots.release()
                with self.job_lock:self.threads.discard(threading.current_thread())
        thread=threading.Thread(target=worker,name='a2n-workflow-install',daemon=True)
        with self.job_lock:self.threads.add(thread)
        thread.start()
        return row

    def stop(self):
        self.stop_event.set()
        with self.job_lock:threads=list(self.threads)
        for thread in threads:thread.join()

    def preview(self,manifest):
        fields={"v","author_did","package_id","version","image","command","card","limits","permissions","proof"}
        if not isinstance(manifest,dict) or set(manifest)!=fields or manifest["v"]!="a2n-container-workflow/1" or not self.verify(manifest["proof"],unsigned(manifest)):
            raise ValueError("UNVERIFIED_WORKFLOW_MANIFEST")
        if not isinstance(manifest["package_id"],str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}",manifest["package_id"]):raise ValueError("INVALID_WORKFLOW_ID")
        image=manifest["image"]
        if not isinstance(image,str) or not re.fullmatch(r"(?:[a-zA-Z0-9./_:-]+@)?sha256:[0-9a-f]{64}",image):raise ValueError("IMMUTABLE_WORKFLOW_IMAGE_REQUIRED")
        command=manifest["command"]
        if not isinstance(command,list) or not 1<=len(command)<=16 or any(not isinstance(c,str) or not 1<=len(c)<=4096 or '\x00' in c for c in command):raise ValueError("INVALID_WORKFLOW_COMMAND")
        limits=manifest["limits"]
        if not isinstance(limits,dict) or set(limits)!={"timeout_ms","memory_mib","gpu"} or type(limits["timeout_ms"]) is not int or not 100<=limits["timeout_ms"]<=60000 or type(limits["memory_mib"]) is not int or not 64<=limits["memory_mib"]<=8192 or type(limits["gpu"]) is not bool:raise ValueError("INVALID_WORKFLOW_RESOURCES")
        permissions={"execute":"CONTAINER","network":"NONE","host_files":"NONE","gpu":limits["gpu"]}
        if manifest["permissions"]!=permissions:raise ValueError("UNSUPPORTED_WORKFLOW_PERMISSIONS")
        card=manifest["card"]
        if not isinstance(card,dict) or not isinstance(manifest["version"],str) or not 1<=len(manifest["version"])<=64 or card.get("version")!=manifest["version"] or not card.get("name") or not isinstance(card.get("skills"),list) or not 1<=len(card["skills"])<=16 or any(not isinstance(s,dict) or not isinstance(s.get("id"),str) or not s["id"] for s in card["skills"]):raise ValueError("INVALID_WORKFLOW_CARD")
        if len(str(manifest))>65536:raise ValueError("WORKFLOW_MANIFEST_LIMIT")
        return {"service_id":"wf_"+digest([manifest["author_did"],manifest["package_id"]])[:24],"manifest":manifest,
                "preview_digest":digest(manifest),"publisher_verified":True,"dependencies":{"immutable_image":image,"docker_required":True},
                "platform_commission_minor":0}

    def install(self,body):
        preview=self.preview(body.get("manifest"));manifest=preview["manifest"]
        if body.get("accepted_digest")!=preview["preview_digest"] or body.get("granted_permissions")!=manifest["permissions"]:raise ValueError("EXPLICIT_WORKFLOW_GRANT_REQUIRED")
        command_id=body.get("command_id")
        if not isinstance(command_id,str) or not 1<=len(command_id)<=128:raise ValueError("WORKFLOW_COMMAND_ID_REQUIRED")
        image_id(manifest["image"])  # Installed dependency checked before mounting. Never silently pulls code.
        with self.store.tx():
            previous=self.store.get("workflow_commands",command_id)
            if previous and previous!=preview["preview_digest"]:raise ValueError("IDEMPOTENCY_CONFLICT")
            old=self.store.get("workflows",preview["service_id"])
            if any(r['service_id']==preview['service_id'] and r['manifest']['version']==manifest['version'] and r['preview_digest']!=preview['preview_digest'] for r in self.store.items('workflow_versions').values()):raise ValueError("WORKFLOW_VERSION_IMMUTABLE")
            row={**preview,"active":True,"execution_granted":True}
            self.store.put("workflow_versions",preview["preview_digest"],row)
            self.store.put("workflows",row["service_id"],row)
            self.store.put("workflow_commands",command_id,row["preview_digest"])
        self.mount(row);return row

    def mount(self,row):
        with self.mount_lock:
            return self._mount(row)

    def _mount(self,row):
        sid=row["service_id"];manifest=row["manifest"];card=dict(manifest["card"])
        card.pop("url",None);card["x-a2n"]={**card.get("x-a2n",{}),"workflow_origin":{"author_did":manifest["author_did"],"package_id":manifest["package_id"],"image":manifest["image"]}}
        old=self.runtime.bindings.get(sid);state=self.store.get("workflow_state",sid,{"listed":False,"enabled":True})
        listed=state["listed"];enabled=state["enabled"]
        if old:self.runtime.bindings.remove(sid)
        binding=self.runtime.mount_callable(card,lambda payload:self.invoke(row,payload),service_id=sid,
                                            metadata={"listed":listed,"workflow_managed":True,"workflow_digest":row["preview_digest"]})
        binding.enabled=enabled

    def invoke(self,row,payload):
        if not self.slots.acquire(blocking=False):raise ValueError("WORKFLOW_CAPACITY_LIMIT")
        try:
            m=row["manifest"];limits=m["limits"]
            result=run_json(m["image"],m["command"],payload,**limits)
            files=result.pop('_a2n_files',[]) if isinstance(result,dict) else []
            if not isinstance(files,list) or len(files)>8:raise ValueError("WORKFLOW_FILE_LIMIT")
            if files:
                import base64
                from a2n_sdk.assets import CHUNK
                if self.assets is None:raise ValueError("WORKFLOW_ASSET_PORT_REQUIRED")
                refs=[]
                for item in files:
                    if not isinstance(item,dict) or set(item)!={"mime_type","base64"}:raise ValueError("INVALID_WORKFLOW_FILE")
                    raw=base64.b64decode(item["base64"],validate=True)
                    refs.append(self.assets.upload(len(raw),item["mime_type"],(raw[i:i+CHUNK] for i in range(0,len(raw),CHUNK))))
                result['assets']=refs
            return result
        finally:self.slots.release()

    def rollback(self,body):
        row=self.store.get("workflow_versions",body.get("version_digest"))
        if not row or row["service_id"]!=body.get("service_id"):raise ValueError("WORKFLOW_VERSION_NOT_FOUND")
        self.preview(row["manifest"]);image_id(row["manifest"]["image"])
        self.store.put("workflows",row["service_id"],row);self.mount(row);return row

    def restore(self):
        for key,row in self.store.items('workflow_installs').items():
            if row['phase'] in {'QUEUED','FETCHING_DEPENDENCY'}:
                self.store.put('workflow_installs',key,{**row,'phase':'RECOVERY_AVAILABLE'})
        for row in self.store.items("workflows").values():
            if row.get("active") and row.get("execution_granted"):
                self.preview(row["manifest"]);self.mount(row)
