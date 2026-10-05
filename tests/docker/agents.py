"""Acceptance upstream services with real HTTP execution and observable counters.
Not loaded by the product image or ordinary node deployment.
"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
counts={}
lock=threading.Lock()
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def reply(self,body,status=200):
        raw=json.dumps(body).encode(); self.send_response(status)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self): self.reply({"counts":dict(counts)})
    def do_POST(self):
        value=json.loads(self.rfile.read(int(self.headers.get("Content-Length","0"))))
        payload=value.get("payload") or {}; skill=value.get("skill")
        with lock: counts[skill]=counts.get(skill,0)+1
        if self.path == "/failure": return self.reply({"error":"upstream_unavailable"},503)
        if self.path == "/slow": time.sleep(3)
        if self.path == "/wrong": return self.reply({"wrong":True})
        if skill == "add": return self.reply({"sum":payload["a"]+payload["b"]})
        self.reply({"echo":payload,"skill":skill})
ThreadingHTTPServer((os.environ.get("A2N_TEST_AGENT_HOST", "0.0.0.0"),
                     int(os.environ.get("A2N_TEST_AGENT_PORT", "9000"))),Handler).serve_forever()
