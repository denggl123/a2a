"""Independent local maintenance worker; business facts are never pruned."""
from __future__ import annotations
import shutil
import threading
import time
from a2n_sdk.archives import archive

class MaintenanceService:
    def __init__(self,home,store,backups,assets):
        self.home,self.store,self.backups,self.assets=home,store,backups,assets
        self.stop_event=threading.Event();self.thread=None;self._lock=threading.Lock()

    def status(self):
        policy=self.store.get("maintenance_settings","policy",{"revision":0,"enabled":False,"interval_hours":24,"keep":7})
        free=shutil.disk_usage(self.home).free
        limits=self.assets.book.limits();rows=self.store.items("assets").values();used=sum(r["size"] for r in rows)
        return {"policy":policy,"backup":self.store.get("maintenance_status","backup",{}),"asset_limits":limits,
                "asset_bytes":used,"disk_free_bytes":free,"capacity_warning":used>=limits["total_bytes"]*.8 or free<1024**3,
                "archived_plans":len(self.store.items("task_review_plans_archive")),"archived_models":len(self.store.items("calibration_models_archive"))}

    def configure(self,body):
        if not isinstance(body,dict) or set(body)-{"enabled","interval_hours","keep","password","expected_revision"}:
            raise ValueError("INVALID_MAINTENANCE_POLICY")
        if type(body.get("enabled")) is not bool or type(body.get("interval_hours")) is not int or not 1<=body["interval_hours"]<=720 or type(body.get("keep")) is not int or not 1<=body["keep"]<=90:
            raise ValueError("INVALID_MAINTENANCE_POLICY")
        with self.store.tx():
            old=self.status()["policy"]
            if type(body.get("expected_revision")) is not int or body["expected_revision"]!=old["revision"]: raise ValueError("REV_CONFLICT")
            password=body.get("password") or self.store.get("maintenance_secrets","backup_password")
            if body["enabled"] and (not isinstance(password,str) or not 12<=len(password)<=1024): raise ValueError("BACKUP_RECOVERY_PASSWORD_REQUIRED")
            if body.get("password"): self.store.put("maintenance_secrets","backup_password",password)
            policy={k:body[k] for k in ("enabled","interval_hours","keep")};policy["revision"]=old["revision"]+1
            self.store.put("maintenance_settings","policy",policy)
        return self.status()

    def archive(self):
        plans=archive(self.store,"task_review_plans",keep=1000,eligible=lambda r:r["state"]!="WAITING")
        active={r["model_id"] for r in self.store.items("calibration_active").values() if r.get("model_id")}
        models=archive(self.store,"calibration_models",keep=32,eligible=lambda r:r["model_id"] not in active)
        return {"plans":plans,"models":models}

    def tick(self,force=False):
        policy=self.status()["policy"]; previous=self.store.get("maintenance_status","backup",{})
        if not policy["enabled"]: return {"state":"DISABLED"}
        if not force and time.time()-previous.get("last_attempt",0)<policy["interval_hours"]*3600: return previous
        if not self._lock.acquire(blocking=False): return {"state":"RUNNING"}
        try:
            self.archive();self.store.put("maintenance_status","backup",{"state":"RUNNING","last_attempt":time.time()})
            row=self.backups.create(self.store.get("maintenance_secrets","backup_password"))
            self.store.put("scheduled_backups",row["backup_id"],row)
            scheduled=sorted(self.store.items("scheduled_backups").values(),key=lambda r:r["created_at"])
            root=(self.home/"backups").resolve()
            from pathlib import Path
            for old in scheduled[:-policy["keep"]]:
                path=Path(old["path"]).resolve()
                if path.parent!=root or path.name!=old["backup_id"]+".a2nbak": raise ValueError("BACKUP_RETENTION_PATH_INVALID")
                path.unlink(missing_ok=True);self.store.delete("scheduled_backups",old["backup_id"])
                self.store.put("backup_retention_history",old["backup_id"],{**old,"retired_at":time.time()})
            result={"state":"READY","backup_id":row["backup_id"],"last_attempt":time.time()}
        except Exception as exc:
            result={"state":"FAILED","error_type":type(exc).__name__,"last_attempt":time.time()}
        finally:self._lock.release()
        self.store.put("maintenance_status","backup",result);return result

    def start(self):
        def worker():
            while not self.stop_event.wait(30):
                try:self.tick()
                except Exception:pass
        self.thread=threading.Thread(target=worker,name="a2n-maintenance",daemon=True);self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join()
        # A manually requested backup can also be in flight when the node stops.
        with self._lock:
            pass
