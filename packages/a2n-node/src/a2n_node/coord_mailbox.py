"""Outbound coordination worker. Independent of optional task relaying."""
from __future__ import annotations

import threading
import time


class CoordinationMailboxClient:
    def __init__(self, network, public, roots):
        self.network, self.public, self.roots = network, public, roots
        self._stop = threading.Event()
        self._thread = None
        self.last_error = ""
        self.registered = False

    def _send(self, session, action, body):
        request = self.network._envelope("HELLO" if action == "register" else "PROBE", body,
                                         session["node_did"])
        result, _, status = self.network._request(session["endpoint"].rstrip("/") + "/mailbox/" + action,
                                                  request, cap=262144, timeout=3)
        self.network._unwrap(result, status, request, session["node_did"])
        return result["body"]

    def _run(self):
        session, registered_at, base, root_index = None, 0, "", 0
        while not self._stop.is_set():
            try:
                roots = list(self.roots())
                if not roots:
                    self.registered = False
                    self.public.mailbox_route = None
                    self._stop.wait(1)
                    continue
                if session is None or base not in roots or time.time() - registered_at > 25:
                    base = roots[root_index % len(roots)]
                    session, _ = self.network.handshake(
                        {"endpoint": base.rstrip("/") + "/public/v1/coord"}, 16384, 3)
                    route = {"endpoint": base.rstrip("/") + "/public/v1/coord/mailbox",
                             "relay_did": session["node_did"]}
                    record = self.public.record(mailbox=route)
                    self._send(session, "register", {"node_record": record})
                    self.public.mailbox_route = route
                    registered_at = time.time()
                    self.registered = True
                result = self._send(session, "poll", {})
                self.last_error = ""
                for request in result.get("requests", [])[:1]:
                    response = self.public.handle(request["type"], request)
                    self._send(session, "reply", {"response": response})
            except Exception as exc:
                self.last_error, self.registered, session = str(exc), False, None
                self.public.mailbox_route = None
                root_index += 1
                self._stop.wait(2)
            self._stop.wait(.75)

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="a2n-coord-mailbox")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

