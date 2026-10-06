"""Ciphertext snapshots and non-payload continuity checks for the four live nodes."""
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / ".tmp" / "vision-live-continuity.json"
PUBLIC_REPORT = ROOT / "artifacts" / "vision-live-continuity.json"
CODE = '''import base64,hashlib,json,sqlite3,time
from pathlib import Path
from a2n_sdk.storage import LocalStore
from a2n_node.protection import system_protector
from a2n_p2p import Identity
home=Path(HOME)
store=LocalStore(home/'runtime.db',system_protector())
try:
    did=Identity.from_private_bytes(base64.b64decode(store.get('identity','seed'))).did
    calls={hashlib.sha256(json.dumps([r[0],r[1]]).encode()).hexdigest():hashlib.sha256(json.dumps(list(r)).encode()).hexdigest()
        for r in store._db.execute('SELECT scope,task_id,fingerprint,created FROM calls')}
    samples={hashlib.sha256(k.encode()).hexdigest():v.get('digest') for k,v in store.items('samples').items()}
    result={'did':did,'calls':calls,'samples':samples}
    if BACKUP:
        target=home/'backups'/('before-vision-'+str(time.time_ns())+'.db')
        target.parent.mkdir(exist_ok=True)
        copied=sqlite3.connect(target)
        with store._lock: store._db.backup(copied)
        assert copied.execute('PRAGMA quick_check').fetchone()[0]=='ok'
        copied.close()
        result['ciphertext_backup']=str(target)
    print(json.dumps(result))
finally: store.close()
'''


def collect(backup):
    results = {}
    home = ROOT / "data" / "desktop-sdk"
    code = "HOME=" + repr(str(home)) + "\nBACKUP=" + repr(backup) + "\n" + CODE
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    results["desktop"] = json.loads(run.stdout)
    for name in ("node-a", "node-b", "node-c"):
        code = "HOME='/app/state/node'\nBACKUP=" + repr(backup) + "\n" + CODE
        run = subprocess.run(["docker", "exec", "-i", "a2n-acceptance-" + name + "-1", "python", "-"],
            input=code, capture_output=True, text=True, check=True)
        results[name] = json.loads(run.stdout)
    return results


def main():
    if sys.argv[1:] == ["before"]:
        result = collect(True)
        REPORT.parent.mkdir(exist_ok=True)
        REPORT.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({name: {"did": row["did"], "calls": len(row["calls"]), "samples": len(row["samples"])} for name, row in result.items()}))
        return
    if sys.argv[1:] != ["after"]:
        raise SystemExit("Use before or after")
    before, after = json.loads(REPORT.read_text(encoding="utf-8")), collect(False)
    checks = []
    for name, row in before.items():
        current = after[name]
        passed = (row["did"] == current["did"] and all(current["calls"].get(k) == v for k, v in row["calls"].items())
                  and all(current["samples"].get(k) == v for k, v in row["samples"].items()))
        checks.append({"node": name, "did": current["did"], "passed": passed, "original_calls": len(row["calls"]),
            "current_calls": len(current["calls"]), "original_samples": len(row["samples"]), "current_samples": len(current["samples"]),
            "identity_retained": row["did"] == current["did"], "ciphertext_backup": row["ciphertext_backup"]})
    report = {"at": time.time(), "passed": all(r["passed"] for r in checks), "checks": checks}
    PUBLIC_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
