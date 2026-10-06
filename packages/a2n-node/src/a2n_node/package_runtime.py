"""Node adapter for portable signed WASM Agents; no Docker or compiler needed."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from a2n_sdk.agent_packages import PackageBook
from .feedback_identity import verifier_for
from .product_cli import runtime_command
from .wasm_worker import AAD


class PackageService:
    def __init__(self, store, runtime):
        self.store, self.runtime = store, runtime
        self.book = PackageBook(store, verifier_for())
        self._slots = threading.BoundedSemaphore(2)

    def preview(self, manifest, module_base64):
        return self.book.preview(manifest, module_base64)

    def install(self, manifest, module_base64, **grant):
        preview = self.preview(manifest, module_base64)
        # Validate the binary before activation; preview never executes it.
        import wasmtime as w
        with w.Engine() as engine:
            w.Module.validate(engine, base64.b64decode(module_base64, validate=True))
        row = self.book.install(preview, **grant)
        self._mount(row)
        return {k: v for k, v in row.items() if k != "module_base64"}

    def _mount(self, row):
        manifest = row["manifest"]
        card = json.loads(json.dumps(manifest["card"]))
        card.pop("url", None)
        card.setdefault("x-a2n", {})["package_origin"] = {"publisher_did": manifest["author_did"],
            "package_id": manifest["package_id"], "module_sha256": manifest["module_sha256"],
            "version": manifest["version"], "royalty": manifest["royalty"], "abi": manifest["abi"]}
        sid = row["service_id"]
        state = self.store.get("agent_package_state", sid) or {"enabled": True, "listed": False}
        if self.runtime.bindings.get(sid):
            self.runtime.bindings.remove(sid)
        binding = self.runtime.mount_callable(card, lambda payload: self._invoke(row, payload), service_id=sid,
            metadata={"package_digest": row["preview_digest"], "package_managed": True, "listed": state["listed"]})
        binding.enabled = state["enabled"]

    def restore(self):
        for row in self.store.items("agent_packages").values():
            if row.get("active") and row.get("execution_granted"):
                validated = self.preview(row["manifest"], row["module_base64"])
                if validated["preview_digest"] != row["preview_digest"]:
                    raise ValueError("STORED_PACKAGE_CHANGED")
                self._mount(row)

    def rollback(self, service_id, version_digest):
        row = self.book.rollback(service_id, version_digest)
        self._mount(row)
        return {k: v for k, v in row.items() if k != "module_base64"}

    def _invoke(self, row, payload):
        if not self._slots.acquire(blocking=False):
            raise ValueError("PACKAGE_CAPACITY_LIMIT")
        try:
            limits = row["manifest"]["limits"]
            raw = json.dumps({"module_base64": row["module_base64"], "payload": payload, "limits": limits},
                             ensure_ascii=False, allow_nan=False).encode()
            if len(raw) > 1500000:
                raise ValueError("PACKAGE_INPUT_TOO_LARGE")
            secret, nonce = os.urandom(32), os.urandom(12)
            cipher = AESGCM(secret)
            with tempfile.TemporaryDirectory(prefix="a2n-agent-worker-") as tmp:
                path = Path(tmp) / "job.sealed"
                path.write_bytes(nonce + cipher.encrypt(nonce, raw, AAD))
                env = {**os.environ, "_A2N_WASM_JOB_KEY": secret.hex(), "PYINSTALLER_RESET_ENVIRONMENT": "1"}
                kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
                try:
                    subprocess.run(runtime_command("a2n_node.wasm_worker", ["--job", str(path)]),
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        env=env, timeout=limits["timeout_ms"] / 1000 + 4, check=True, **kwargs)
                except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
                    raise ValueError("PACKAGE_WORKER_FAILED_OR_TIMED_OUT") from exc
                blob = path.with_suffix(".result").read_bytes()
                result = json.loads(cipher.decrypt(blob[:12], blob[12:], AAD))
                if not result.get("ok"):
                    raise ValueError("PACKAGE_EXECUTION_REJECTED: " + str(result.get("error", "")))
                return result["result"]
        finally:
            self._slots.release()
