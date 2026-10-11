"""Read-only source, release and optional installed four-node health report.

No Agent execution, payment, wallet export, database writes or configuration
changes. Only allowlisted summary fields enter the public report.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from build_sdk_bundle import ROOT, PACKAGES, source_digest, source_hashes, release_files
from release_check import check_metadata, check_desktop, check

NODES = ("desktop", "node-a", "node-b", "node-c")
SOURCE_CHECK = '''import hashlib,importlib,json,pathlib,sys
expected=json.load(sys.stdin)
actual={}
for module in expected:
 root=pathlib.Path(importlib.import_module(module).__file__).parent
 actual[module]={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
  for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in {'.pyc','.pyo'}}
print(json.dumps({'matched':actual==expected,'files':sum(map(len,actual.values()))}))
'''


def run(args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          timeout=45, cwd=ROOT, **kwargs)


def audit(installed=False):
    report = {"at": datetime.now(timezone.utc).isoformat(), "read_only": True,
              "scope": "SOURCE_RELEASE" + ("_INSTALLED_LOCAL_FOUR_NODES" if installed else ""),
              "checks": [], "nodes": [], "attention": []}

    def gate(name, fn):
        try:
            facts = fn()
            passed = facts.get("passed", True) if isinstance(facts, dict) else True
            report["checks"].append({"name": name, "passed": passed, "facts": facts})
        except Exception as exc:
            # Error strings can contain owner input or credentials. Do not publish them.
            report["checks"].append({"name": name, "passed": False, "error_type": type(exc).__name__})

    hashes = source_hashes(release_files())
    report["source_sha256"] = source_digest(hashes)
    report["version"] = (ROOT / "VERSION").read_text().strip()
    gate("metadata_and_current_docs", lambda: {"version": check_metadata()})
    gate("source_archive_current", lambda: {"sha256": check(ROOT / "artifacts/a2n-sdk-server.tar.gz", hashes)["sha256"]})
    gate("desktop_release_current_and_signed", lambda: check_desktop(report["version"], hashes, require_signed=True))
    tracked = run(["git", "ls-files", "-z"], check=True).stdout.split("\0")
    junk = [p for p in tracked if p and (p.startswith((".pytest-tmp", ".pytest_cache/", ".tmp/", "data/"))
            or "__pycache__" in p.split("/") or p.endswith((".pyc", ".pyo")))]
    gate("no_tracked_runtime_or_test_data", lambda: {"passed": not junk, "count": len(junk)})
    if installed:
        from local_business_acceptance import api
        expected = {}
        for package in PACKAGES:
            module = package.replace("-", "_")
            directory = ROOT / "packages" / package / "src" / module
            expected[module] = {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in directory.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}}
        for node in NODES:
            def inspect_node(node=node):
                runtime = api(node, "/v1/runtime")
                automatic = api(node, "/v1/trades/automatic")
                jobs = automatic["jobs"]
                public = runtime["public_service"]["services"]
                product = runtime["product_runtime"]
                modules = {key: bool(runtime.get(key)) for key in
                           ("selection", "task_quality", "calibration", "maintenance", "reviewers")}
                if node == "desktop":
                    manifest = json.loads((ROOT / "artifacts/A2N-desktop.manifest.json").read_text(encoding="utf-8"))
                    current = (product.get("mode") == "BUNDLED" and product.get("source_sha256") == report["source_sha256"]
                               and product.get("executable_sha256") == manifest["sha256"])
                else:
                    output = run(["docker", "exec", "-i", "a2n-acceptance-" + node + "-1", "python", "-c", SOURCE_CHECK],
                                 input=json.dumps(expected), check=True)
                    current = json.loads(output.stdout)["matched"]
                pending = automatic.get("summary", {}).get("unfinished_jobs", sum(not job.get("finished") for job in jobs))
                row = {"node": node, "did": runtime["node_did"], "version": product.get("version"),
                       "current_sources": current, "modules": modules,
                       "discovery_required": public.get("discovery"), "samples_required": public.get("samples"),
                       "bindings": len(runtime.get("bindings") or []), "workflows": len(runtime.get("workflows") or []),
                       "automatic_jobs_visible": len(jobs), "automatic_unfinished": pending,
                       "automatic_summary": automatic.get("summary"),
                       "automatic_window": "latest_100_jobs"}
                report["nodes"].append(row)
                if pending:
                    report["attention"].append({"node": node, "kind": "UNFINISHED_ORIGINAL_TRADES", "visible_count": pending})
                images = {item.get("manifest", {}).get("image") for item in runtime.get("workflows", [])
                          if item.get("active") or item.get("execution_granted")}
                settings = runtime.get("reviewers") or {}
                images.update(settings.get(k) for k in ("python_image", "media_image"))
                missing = [image for image in sorted(x for x in images if x)
                           if run(["docker", "image", "inspect", image, "--format", "{{.Id}}"], check=False).returncode]
                row["missing_execution_images"] = len(missing)
                return {"passed": current and product.get("version") == report["version"] and all(modules.values())
                        and public.get("discovery") is True and public.get("samples") is True and not missing}
            gate("installed_" + node, inspect_node)
        def container_images():
            output = run(["docker", "ps", "--format", "{{.Names}}"], check=True)
            containers = []
            for name in output.stdout.splitlines():
                if not name.startswith("a2n-"):
                    continue
                image = run(["docker", "inspect", name, "--format", "{{.Image}}"], check=True).stdout.strip()
                present = run(["docker", "image", "inspect", image, "--format", "{{.Id}}"], check=False).returncode == 0
                containers.append({"name": name, "image_available": present})
            return {"passed": bool(containers) and all(c["image_available"] for c in containers), "containers": containers}
        gate("running_container_images_recreatable", container_images)
    report["passed"] = all(item["passed"] for item in report["checks"])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed", action="store_true", help="Read the existing Windows + three Docker nodes")
    parser.add_argument("--report", type=Path, default=ROOT / "artifacts/project-health.json")
    args = parser.parse_args(argv)
    report = audit(args.installed)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": len(report["checks"]),
                      "failed": [c["name"] for c in report["checks"] if not c["passed"]],
                      "attention": report["attention"], "report": str(args.report)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
