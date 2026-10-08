"""Finish upgrade verification against authenticated, pre-upgrade backups."""
import json
from pathlib import Path
import subprocess

from a2n_node.backups import opened_backup
from a2n_node.protection import system_protector
from a2n_node.upgrades import file_digest
from a2n_sdk.storage import LocalStore
from a2n_sdk.trials import TrialBook
from local_business_acceptance import api

ROOT = Path(__file__).resolve().parents[1]


def main():
    private = ROOT / ".tmp/points-release-secrets.bin"
    secrets = json.loads(system_protector().open(private.read_bytes()))
    nodes = {}
    for node, row in secrets.items():
        if node == "desktop":
            store = LocalStore(ROOT / "data/desktop-sdk/runtime.db", system_protector())
            try:
                pending = store.get("release_pending", row["pending_id"])
                path = Path(pending["backup_path"])
            finally:
                store.close()
        else:
            path = ROOT / ".tmp" / ("points-before-" + node + ".a2nbak")
            subprocess.run(["docker", "cp", "a2n-acceptance-" + node + "-1:" + row["backup"]["path"], str(path)],
                check=True, capture_output=True)
        current = api(node, "/v1/runtime")
        with opened_backup(path, row["password"]) as (backup, metadata, _):
            assert current["node_did"] == metadata["node_did"]
            assert current["trial_counts"] == TrialBook(backup).counts()
        assert api(node, "/v1/points")["method"] == "a2n-points/1"
        nodes[node] = {"did": current["node_did"], "samples_and_counts_preserved": True,
            "points_installed": True, "trial_counts": current["trial_counts"]}
    digest = file_digest(ROOT / "artifacts/A2N.exe")
    assert api("desktop", "/v1/runtime")["product_runtime"]["executable_sha256"] == digest
    image = subprocess.check_output(["docker", "image", "inspect", "a2n-node-test", "--format", "{{.Id}}"], text=True).strip()
    report = {"passed": True, "desktop_sha256": digest, "docker_image": image,
        "backups": {n: {"created": True, "authenticated": True} for n in secrets},
        "recovery_secrets": str(private), "nodes": nodes}
    (ROOT / "artifacts/points-deployment.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Four installed nodes verified against pre-upgrade authenticated backups.")


if __name__ == "__main__":
    main()
