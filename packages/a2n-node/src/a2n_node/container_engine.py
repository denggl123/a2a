"""Bounded disposable containers. No host mounts, network, or owner credentials."""
from __future__ import annotations
import json
import os
import secrets
import subprocess
import threading

def run_json(image,command,payload,*,timeout_ms=10000,memory_mib=256,gpu=False,input_limit=1024**2):
    name="a2n-exec-"+secrets.token_hex(10)
    args=["docker","run","--rm","-i","--name",name,"--pull=never","--network=none","--read-only",
          "--cap-drop=ALL","--security-opt=no-new-privileges","--pids-limit=32","--memory",str(memory_mib)+"m",
          "--cpus=1","--user=65534:65534","--tmpfs=/tmp:rw,noexec,nosuid,size=64m"]
    if gpu: args += ["--gpus","device=0"]
    args += ["--entrypoint",command[0],image,*command[1:]]
    raw=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode()
    if len(raw)>min(input_limit,16*1024**2): raise ValueError("CONTAINER_INPUT_LIMIT")
    kwargs={"creationflags":subprocess.CREATE_NO_WINDOW} if os.name=="nt" else {}
    process=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,**kwargs)
    chunks=[];size=[0];overflow=threading.Event()
    def feed():
        try:process.stdin.write(raw);process.stdin.close()
        except (BrokenPipeError,OSError):pass
    def drain():
        pending=b''
        while True:
            chunk=process.stdout.read1(8192)
            if not chunk:break
            size[0]+=len(chunk)
            if size[0]>1024**2:overflow.set();process.kill();break
            pending+=chunk
            while b'\n' in pending:
                line,pending=pending.split(b'\n',1)
                try:frame=json.loads(line)
                except (ValueError,UnicodeError):frame=None
                if isinstance(frame,dict) and frame.get('event')=='progress':
                    from a2n_sdk.progress import emit
                    emit(frame.get('delta',''))
                else:chunks.append(line+b'\n')
        if pending:chunks.append(pending)
    threading.Thread(target=feed,daemon=True).start()
    import contextvars
    context=contextvars.copy_context();reader=threading.Thread(target=context.run,args=(drain,),daemon=True);reader.start()
    try:
        code=process.wait(timeout=timeout_ms/1000+2);reader.join(1)
        if overflow.is_set():raise ValueError("CONTAINER_OUTPUT_LIMIT")
        if code:raise ValueError("CONTAINER_EXECUTION_FAILED")
        return json.loads(b"".join(chunks))
    except subprocess.TimeoutExpired as exc:
        process.kill();process.wait(timeout=5);raise ValueError("CONTAINER_TIMEOUT") from exc
    finally:
        subprocess.run(["docker","rm","-f",name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10,**kwargs)
        process.stdout.close()

def image_id(image):
    kwargs={"creationflags":subprocess.CREATE_NO_WINDOW} if os.name=="nt" else {}
    result=subprocess.run(["docker","image","inspect",image,"--format","{{.Id}}"],capture_output=True,text=True,timeout=10,**kwargs)
    value=result.stdout.strip()
    if result.returncode or not value.startswith("sha256:") or len(value)!=71:raise ValueError("CONTAINER_IMAGE_NOT_INSTALLED")
    return value
