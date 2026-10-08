"""Back up and upgrade the existing four local nodes, preserving their homes."""
import base64
import argparse
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time

from a2n_p2p import Identity
from a2n_node.protection import system_protector
from a2n_node.feedback_identity import signer_for
from a2n_node.upgrades import file_digest
from a2n_sdk.experience import signed
from a2n_sdk.storage import LocalStore
from local_business_acceptance import api

ROOT = Path(__file__).resolve().parents[1]
NODES = ("desktop", "node-a", "node-b", "node-c")


def main(report_prefix="completion"):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", report_prefix):
        raise ValueError("Invalid local deployment report prefix")
    before = {n: api(n, "/v1/runtime") for n in NODES}
    passwords = {n: secrets.token_urlsafe(32) for n in NODES}
    secret_rows, backups = {}, {}
    for n in NODES:
        if n != "desktop":
            backups[n] = api(n, "/v1/backups", {"password": passwords[n]})
            secret_rows[n] = {"password": passwords[n], "backup": backups[n]}
    home = ROOT / "data/desktop-sdk"
    store = LocalStore(home / "runtime.db", system_protector())
    try:
        identity = Identity.from_private_bytes(base64.b64decode(store.get("identity", "seed")))
        publisher = store.get("release_publishers", identity.did) or {}
    finally:
        store.close()
    exe = ROOT / "artifacts/A2N.exe"
    digest = file_digest(exe)
    manifest = signed({"v": "a2n-release/1", "author_did": identity.did,
        "release_sequence": publisher.get("installed_sequence", 0) + 1, "platform": "Windows",
        "artifact": "A2N.exe", "size": exe.stat().st_size, "sha256": digest, "database_schema": 2}, signer_for(identity))
    api("desktop", "/v1/upgrades/trust", {"author_did": identity.did, "trusted": True})
    pending = api("desktop", "/v1/upgrades/prepare", {"manifest": manifest, "path": str(exe), "password": passwords["desktop"]})
    secret_rows["desktop"] = {"password": passwords["desktop"], "pending_id": pending["id"], "backup": pending.get("backup")}
    private = ROOT / (".tmp/" + report_prefix + "-release-secrets.bin")
    private.parent.mkdir(exist_ok=True)
    if private.exists():
        shutil.copy2(private, private.with_name(report_prefix + "-release-secrets-" + secrets.token_hex(6) + ".bin"))
    private.write_bytes(system_protector().seal(json.dumps(secret_rows).encode()))
    api("desktop", "/v1/upgrades/apply", {"pending_id": pending["id"]})
    print("Windows SDK upgrade queued with encrypted backup", flush=True)
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        try:
            now = api("desktop", "/v1/runtime")
            if now["product_runtime"].get("executable_sha256") == digest:
                break
        except Exception:
            pass
        time.sleep(.5)
    else:
        raise RuntimeError("Windows SDK did not activate the expected release")
    subprocess.run([sys.executable, str(ROOT / "scripts/verify_desktop_runtime.py"), str(home), digest, "--activate"],
        check=True, cwd=ROOT)
    api("desktop", "/v1/upgrades/trust", {"author_did": identity.did, "trusted": bool(publisher.get("trusted"))})
    compose = ["docker", "compose", "-f", str(ROOT / "docker/acceptance.yaml")]
    network = ROOT / ".tmp/network.env"
    if network.exists():
        compose += ["-f", str(ROOT / "docker/network.yaml")]
    compose += ["--env-file", str(network if network.exists() else ROOT / ".tmp/acceptance.env"),
        "up", "-d", "--no-deps", "--force-recreate", "node-a", "node-b", "node-c"]
    subprocess.run(compose, check=True, cwd=ROOT)
    after = {}
    for n in NODES:
        for attempt in range(30):
            try:
                after[n] = api(n, "/v1/runtime")
                points = api(n, "/v1/points")
                onboarding = api(n, "/v1/onboarding")
                assert onboarding["node_did"] == before[n]["node_did"]
                assert "outbound_settlement" in onboarding
                assert points["method"] == "a2n-points/1"
                assert after[n]["node_did"] == before[n]["node_did"]
                assert after[n]["trial_counts"] == before[n]["trial_counts"]
                break
            except Exception:
                if attempt == 29:
                    raise
                time.sleep(.5)
    image = subprocess.check_output(["docker", "image", "inspect", "a2n-node-test", "--format", "{{.Id}}"], text=True).strip()
    report = {"passed": True, "desktop_sha256": digest, "docker_image": image,
        "backups": {n: {"created": True} for n in NODES}, "recovery_secrets": str(private),
        "nodes": {n: {"did": after[n]["node_did"], "samples_and_counts_preserved": True,
            "points_installed": True, "settlement_and_onboarding_installed": True, "trial_counts": after[n]["trial_counts"]} for n in NODES}}
    (ROOT / ("artifacts/" + report_prefix + "-deployment.json")).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Four existing node identities and sample ledgers preserved", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-prefix", default="completion")
    main(parser.parse_args().report_prefix)
