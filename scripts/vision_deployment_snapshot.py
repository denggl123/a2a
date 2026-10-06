"""Read-only proof that the four live nodes use the current product sources."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from network_acceptance import api

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("kernel", "p2p", "acceptance", "sdk", "node")
CODE = '''import hashlib,importlib,json,sys
expected=json.load(sys.stdin)
actual={}
for module in expected:
    root=__import__('pathlib').Path(importlib.import_module(module).__file__).parent
    actual[module]={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in {'.pyc','.pyo'}}
print(json.dumps({'matched':expected==actual,'files':sum(map(len,actual.values())),
    'different':[m+'/'+k for m in expected for k in set(expected[m])|set(actual[m]) if expected[m].get(k)!=actual[m].get(k)]}))
'''


def main():
    expected = {}
    for name in PACKAGES:
        module = "a2n_" + name
        root = ROOT / "packages" / ("a2n-" + name) / "src" / module
        expected[module] = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}}
    desktop = json.loads((ROOT / "artifacts" / "A2N-desktop.manifest.json").read_text(encoding="utf-8"))
    nodes = []
    for name in ("desktop", "node-a", "node-b", "node-c"):
        runtime = api(name, "/v1/runtime")
        row = {"node": name, "did": runtime["node_did"], "product_runtime": runtime["product_runtime"],
            "discovery_enabled": runtime["public_service"]["services"]["discovery"],
            "file_mailbox_protocol": runtime["experience"]["asset_mailbox"]["protocol"]}
        if name == "desktop":
            row["passed"] = runtime["product_runtime"].get("executable_sha256") == desktop["sha256"] and runtime["product_runtime"]["mode"] == "BUNDLED"
        else:
            run = subprocess.run(["docker", "exec", "-i", "a2n-acceptance-" + name + "-1", "python", "-c", CODE],
                input=json.dumps(expected), text=True, capture_output=True, check=True)
            row["sources"] = json.loads(run.stdout)
            row["passed"] = row["sources"]["matched"]
            image = subprocess.run(["docker", "inspect", "--format", "{{.Image}}", "a2n-acceptance-" + name + "-1"],
                capture_output=True, text=True, check=True)
            row["image"] = image.stdout.strip()
        row["passed"] &= row["discovery_enabled"] and row["file_mailbox_protocol"] == "a2n-asset-mailbox/1"
        nodes.append(row)
    report = {"at": time.time(), "passed": all(n["passed"] for n in nodes), "nodes": nodes}
    (ROOT / "artifacts" / "vision-deployment.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
