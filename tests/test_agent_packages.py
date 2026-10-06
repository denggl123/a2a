import base64
import hashlib
import os

import pytest
import wasmtime

from a2n_node.daemon import Daemon
from a2n_node.feedback_identity import signer_for
from a2n_node.protection import EnvironmentProtector
from a2n_sdk.agent_packages import ABI, PERMISSIONS, VERSION
from a2n_sdk.experience import signed
from a2n_sdk.ports import CallRequest

ECHO = '''(module
 (import "a2n" "read_input" (func $read (param i32 i32)(result i32)))
 (import "a2n" "write_output" (func $write (param i32 i32)(result i32)))
 (memory (export "memory") 1)
 (func (export "run") (call $write (i32.const 0) (call $read (i32.const 0)(i32.const 60000))) drop))'''


def package(identity, wat=ECHO, version="1"):
    module = wasmtime.wat2wasm(wat)
    manifest = signed({"v": VERSION, "author_did": identity.did, "package_id": "normalize-input", "version": version,
        "abi": ABI, "module_sha256": hashlib.sha256(module).hexdigest(),
        "card": {"name": "数据规范化", "description": "读取 JSON 并保留数据结构", "version": version,
            "skills": [{"id": "normalize", "name": "normalize"}],
            "x-a2n": {"price_hint": {"normalize": {"unit": "fen_per_call", "amount": 0}}}},
        "permissions": PERMISSIONS, "limits": {"memory_bytes": 65536, "fuel": 10000,
            "output_bytes": 65536, "timeout_ms": 1000}, "royalty": {"creator_did": identity.did, "share_bps": 500}}, signer_for(identity))
    return manifest, base64.b64encode(module).decode()


def install(node, manifest, module):
    preview = node.packages.preview(manifest, module)
    return node.packages.install(manifest, module, granted_permissions=PERMISSIONS,
        accepted_digest=preview["preview_digest"], command_id=preview["preview_digest"])


def test_actual_signed_package_runs_in_worker_can_publish_trade_and_survives_restart_without_resetting_samples(tmp_path):
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    provider = Daemon(tmp_path / "provider", port=0, protector=protector).start()
    buyer = Daemon(tmp_path / "buyer", port=0, protector=protector).start()
    try:
        manifest, module = package(provider.identity)
        preview = provider.packages.preview(manifest, module)
        assert not preview["execution_granted"] and not provider.store.recent()
        with pytest.raises(ValueError, match="GRANT_REQUIRED"):
            provider.packages.install(manifest, module, granted_permissions=PERMISSIONS,
                accepted_digest="0" * 64, command_id="bad")
        row = install(provider, manifest, module)
        sid = row["service_id"]
        assert not provider.management.is_listed(provider.runtime.bindings.get(sid))
        provider.management.command("/v1/publish", {"service_id": sid})
        buyer.runtime.import_agent(provider.runtime.project_binding(sid), projection_id="use")
        result = buyer.calls.invoke("use", CallRequest(task_id="first", skill="normalize", payload={"z": 1, "a": "真实输入"}))
        assert result.ok and result.result == {"z": 1, "a": "真实输入"}
        assert result.metadata["admission_contract"]["bilateral_conditions_verified"]
        assert "contract_error" not in result.metadata and result.metadata["trade_facts"]["execution"] == "DELIVERED"
        identity, samples = provider.identity.did, provider.trials.samples(sid)
        provider.stop()
        provider = Daemon(tmp_path / "provider", port=0, protector=protector).start()
        assert provider.identity.did == identity and provider.trials.samples(sid) == samples
        assert provider.management.is_listed(provider.runtime.bindings.get(sid))
        manifest2, module2 = package(provider.identity, version="2")
        newer = install(provider, manifest2, module2)
        assert newer["service_id"] == sid and provider.trials.status(sid)["used"] == 1
        provider.packages.rollback(sid, row["preview_digest"])
        assert provider.runtime.bindings.get(sid).source_card["version"] == "1"
        provider.management.command("/v1/bindings/remove", {"service_id": sid})
        provider.stop()
        provider = Daemon(tmp_path / "provider", port=0, protector=protector).start()
        assert not provider.runtime.bindings.get(sid) and provider.trials.samples(sid) == samples
    finally:
        buyer.stop()
        provider.stop()


@pytest.mark.parametrize("wat", [
    '(module (memory (export "memory") 1) (func (export "run") (loop $forever (br $forever))))',
    '(module (import "env" "read_secret_file" (func $read)) (memory (export "memory") 1) (func (export "run") call $read))',
    '(module (memory (export "memory") 2) (func (export "run")))'])
def test_fuel_memory_and_host_import_limits_reject_execution_and_do_not_create_samples(tmp_path, wat):
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    node = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        manifest, module = package(node.identity, wat)
        row = install(node, manifest, module)
        result = node.calls.invoke(row["service_id"], CallRequest(task_id="limit", skill="normalize", payload="x"))
        assert not result.ok and result.state == "FAILED"
        assert node.trials.status(row["service_id"])["completed"] == 0
        assert not node.trials.samples(row["service_id"])
    finally:
        node.stop()
