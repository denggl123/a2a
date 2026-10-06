"""Exercise the compiled full SDK in isolated homes; never touch the live node."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import time
import urllib.request

from PIL import Image
import wasmtime

from a2n_node.backups import restore_offline
from a2n_node.daemon import Daemon
from a2n_node.feedback_identity import signer_for
from a2n_node.protection import system_protector
from a2n_node.product_cli import health, start_background
from a2n_node.upgrades import file_digest
from a2n_p2p import Identity
from a2n_sdk.agent_packages import ABI, PERMISSIONS, VERSION
from a2n_sdk.client import NodeClient
from a2n_sdk.experience import signed
from a2n_sdk.ports import CallRequest
from a2n_sdk.storage import LocalStore

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / ".tmp" / "vision-product"
PORT = 18772
EXE = ROOT / "artifacts" / "A2N.exe"


def client(home, port):
    store = LocalStore(home / "runtime.db", system_protector())
    try:
        access = store.get("runtime_control", "access")
    finally:
        store.close()
    return NodeClient(f"http://127.0.0.1:{port}", token=access["token"])


def stop(home, port):
    if health(port, home):
        client(home, port).request("POST", "/v1/node/stop", {})
        end = time.monotonic() + 15
        while health(port, home) and time.monotonic() < end:
            time.sleep(.1)
        if health(port, home):
            raise RuntimeError("Isolated node did not stop")


def main():
    assert ROOT / ".tmp" in HOME.resolve().parents and PORT != 8771
    run_id = secrets.token_hex(8)
    report = {"compiled_runtime": True, "at": time.time(), "run_id": run_id, "checks": [], "production_data_touched": False}
    def check(name, value):
        report["checks"].append({"name": name, "passed": bool(value)})
        if not value:
            raise RuntimeError(name)
    buyer, root, restored = None, None, HOME.with_name("vision-product-restored-" + run_id)
    try:
        root = Daemon(ROOT / ".tmp" / ("vision-asset-root-" + run_id), port=0, protector=system_protector(),
            coord_allow_networks=["127.0.0.0/8"]).start()
        NodeClient(root.runtime.local_base_url, token=root.runtime.management_token).request("POST", "/v1/public-services", {"services": {"blob_cache": True}})
        start_background(HOME, PORT, [], executable=str(EXE))
        api = client(HOME, PORT)
        for address in api.request("GET", "/v1/runtime")["connections"]["public_nodes"]:
            if address != root.runtime.local_base_url:
                api.request("POST", "/v1/peers/disconnect", {"address": address})
        api.request("POST", "/v1/peers/connect", {"address": root.runtime.local_base_url})
        original = api.request("GET", "/v1/runtime")
        check("Listener uses the expected complete executable", original["product_runtime"]["mode"] == "BUNDLED" and original["product_runtime"]["executable_sha256"] == file_digest(EXE))
        identity = Identity.generate()
        wat = '''(module (import "a2n" "read_input" (func $r (param i32 i32)(result i32)))
        (import "a2n" "write_output" (func $w (param i32 i32)(result i32)))
        (memory (export "memory") 1)(func (export "run")
        (call $w (i32.const 0)(call $r (i32.const 0)(i32.const 60000))) drop))'''
        module = wasmtime.wat2wasm(wat)
        manifest = signed({"v": VERSION, "author_did": identity.did, "package_id": "native-acceptance-" + run_id,
            "version": "1", "abi": ABI, "module_sha256": hashlib.sha256(module).hexdigest(),
            "card": {"name": "完整运行时输入规范化验收", "version": "1", "skills": [{"id": "normalize"}],
                "x-a2n": {"public_sample_policy": {"safe_output": True}, "price_hint": {"normalize": {"unit": "fen_per_call", "amount": 0}}}},
            "permissions": PERMISSIONS, "limits": {"memory_bytes": 65536, "fuel": 10000, "output_bytes": 65536, "timeout_ms": 1000},
            "royalty": {"creator_did": identity.did, "share_bps": 0}}, signer_for(identity))
        body = {"manifest": manifest, "module_base64": base64.b64encode(module).decode()}
        preview = api.request("POST", "/v1/packages/preview", body)
        installed = api.request("POST", "/v1/packages/install", {**body, "accepted_digest": preview["preview_digest"],
            "granted_permissions": PERMISSIONS, "command_id": "install-" + run_id})
        sid = installed["service_id"]
        api.request("POST", "/v1/publish", {"service_id": sid})
        image = io.BytesIO()
        Image.new("RGB", (700, 400), (27, 75, 90)).save(image, format="PNG")
        raw = image.getvalue()
        request = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/assets/upload", raw,
            {"X-A2N-Local-Token": api.token, "Content-Type": "image/png"}, method="POST")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request) as response:
            asset = json.loads(response.read())
        buyer = Daemon(ROOT / ".tmp" / ("vision-buyer-" + run_id), port=0, protector=system_protector(),
                       coord_allow_networks=["127.0.0.0/8"]).start()
        card = api.request("GET", f"/a2a/{sid}/.well-known/agent.json")
        buyer.runtime.import_agent(card, projection_id="use")
        result = buyer.calls.invoke("use", CallRequest(task_id="native-file-" + run_id, skill="normalize", payload={"assets": [asset]},
            metadata={"a2nSampleConsent": {"input_public": True, "output_public": True}}))
        check("Actual bundled WASM worker delivered a signed cross-node result", result.ok and bool(result.receipt))
        check("Bilateral fixed free contract verified", bool(result.metadata.get("admission_contract", {}).get("bilateral_conditions_verified")) and "contract_error" not in result.metadata)
        facts = buyer.trade_facts.for_call("use", result.task_id)
        local = buyer.assets.fetch(facts["trade_uid"], asset["asset_id"])
        check("Encrypted signed binary ranges matched the original image", b"".join(buyer.assets.book.chunks(local["asset_id"])) == raw)
        buyer.public_directories.add(root.runtime.local_base_url)
        # A second real cross-node delivery allows an uncached download while a
        # firewall denies direct file reads; the provider is the bundled EXE.
        second_raw = b"private-native-NAT-asset\0" + os.urandom(65570)
        upload = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/assets/upload", second_raw,
            {"X-A2N-Local-Token": api.token, "Content-Type": "application/octet-stream"}, method="POST")
        with opener.open(upload) as response:
            second_asset = json.loads(response.read())
        second_result = buyer.calls.invoke("use", CallRequest(task_id="native-nat-" + run_id,
            skill="normalize", payload={"assets": [second_asset]}))
        second_facts = buyer.trade_facts.for_call("use", second_result.task_id)
        until = time.monotonic() + 8
        while time.monotonic() < until:
            lease = api.request("GET", "/v1/runtime")["experience"]["asset_mailbox"].get("lease")
            if lease:
                break
            time.sleep(.1)
        check("Bundled provider registered an outbound file mailbox", bool(lease))
        original_request = buyer.coord_network._request
        def block_file_direct(endpoint, *args, **kwargs):
            if endpoint.startswith(f"http://127.0.0.1:{PORT}/public/v1/assets/"):
                raise PermissionError("Isolated firewall denies direct files")
            return original_request(endpoint, *args, **kwargs)
        buyer.coord_network._request = block_file_direct
        second_local = buyer.assets.fetch(second_facts["trade_uid"], second_asset["asset_id"])
        check("Bundled provider served encrypted NAT file ranges", b"".join(buyer.assets.book.chunks(second_local["asset_id"])) == second_raw and not root.store.items("assets"))
        buyer.coord_network._request = original_request
        before_upgrade = api.request("GET", "/v1/runtime")
        sample = before_upgrade["trials"][sid]["samples"][0]
        check("Bundled Pillow encoded a bounded public thumbnail", len(sample["media_preview"]) == 1 and sample["media_preview"][0]["width"] <= 192)
        HOME.mkdir(parents=True, exist_ok=True)
        (HOME / "installation.json").write_text(json.dumps({"executable": str(EXE), "port": PORT,
            "home": str(HOME), "autostart": False}), encoding="utf-8")
        publisher = Identity.generate()
        release = signed({"v": "a2n-release/1", "author_did": publisher.did, "release_sequence": 1,
            "platform": "Windows", "artifact": "A2N.exe", "size": EXE.stat().st_size,
            "sha256": file_digest(EXE), "database_schema": 2}, signer_for(publisher))
        api.request("POST", "/v1/upgrades/trust", {"author_did": publisher.did, "trusted": True})
        password = "native recovery " + secrets.token_hex(16)
        pending = api.request("POST", "/v1/upgrades/prepare", {"manifest": release, "path": str(EXE), "password": password})
        api.request("POST", "/v1/upgrades/apply", {"pending_id": pending["id"]})
        end, final = time.monotonic() + 55, None
        while time.monotonic() < end:
            if health(PORT, HOME):
                try:
                    final = client(HOME, PORT).request("GET", "/v1/runtime")
                    state = next(p["state"] for p in final["upgrades"]["pending"] if p["id"] == pending["id"])
                    if state in {"INSTALLED", "FAILED", "RECOVERY_REQUIRED", "ROLLED_BACK_BINARY"}:
                        break
                except (OSError, ValueError):
                    pass
            time.sleep(.2)
        check("Actual signed upgrade worker installed the new program", final and next(p["state"] for p in final["upgrades"]["pending"] if p["id"] == pending["id"]) == "INSTALLED")
        check("Upgrade retained DID and exact delivery samples", final["node_did"] == original["node_did"] and final["trials"][sid] == before_upgrade["trials"][sid])
        stop(HOME, PORT)
        recovery = restore_offline(pending["backup_path"], password, restored, system_protector())
        start_background(restored, PORT + 1, [], executable=str(EXE))
        final = client(restored, PORT + 1).request("GET", "/v1/runtime")
        check("Offline portable restore retained DID and sample history", recovery["node_did"] == original["node_did"] and final["trials"][sid] == before_upgrade["trials"][sid])
        stored = LocalStore(restored / "runtime.db", system_protector())
        try:
            from a2n_sdk.assets import AssetBook
            check("Portable restore retained decryptable full private asset", b"".join(AssetBook(stored, node_did=final["node_did"]).chunks(asset["asset_id"])) == raw)
        finally:
            stored.close()
        report.update(passed=True, node_did=original["node_did"], checks_count=len(report["checks"]), executable_sha256=file_digest(EXE))
    except Exception as exc:
        report.update(passed=False, error=str(exc))
        raise
    finally:
        if buyer:
            buyer.stop()
        stop(HOME, PORT)
        stop(restored, PORT + 1)
        if root:
            root.stop()
        (ROOT / "artifacts" / "vision-desktop-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": len(report["checks"])}))


if __name__ == "__main__":
    main()
