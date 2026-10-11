"""Build a Windows executable containing the runtime, all five packages and UI."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULES = ("a2n_kernel", "a2n_p2p", "a2n_acceptance", "a2n_sdk", "a2n_node")


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unsigned', action='store_true', help='Build a CI candidate without publisher keys')
    options = parser.parse_args()
    from release_check import check_metadata
    from build_sdk_bundle import source_hashes, source_digest, release_files
    version = check_metadata()
    hashes = source_hashes(release_files())
    stamp = ROOT / '.tmp/build-info.json'
    stamp.parent.mkdir(exist_ok=True)
    (ROOT / 'artifacts').mkdir(exist_ok=True)
    from third_party_licenses import collect
    licenses = collect(ROOT)
    stamp.write_text(json.dumps({'version': version, 'source_sha256': source_digest(hashes)}), encoding='utf-8')
    if os.name != "nt":
        raise SystemExit("Windows SDK bundle must be built on Windows")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--version"], check=True)
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed", "--noupx",
        "--name", "A2N", "--distpath", str(ROOT / "artifacts"),
        "--workpath", str(ROOT / ".tmp" / "desktop-build"), "--specpath", str(ROOT / ".tmp"),
        "--collect-data", "a2n_sdk", "--add-data", str(stamp) + os.pathsep + 'a2n_node',
        "--add-data", str(ROOT / 'LICENSE') + os.pathsep + '.',
        "--add-data", str(ROOT / 'NOTICE') + os.pathsep + '.',
        "--add-data", str(licenses) + os.pathsep + 'third-party-licenses']
    args += ["--collect-all", "wasmtime"]
    args += ["--collect-submodules", "PIL"]
    args += ["--collect-all", "eth_account", "--collect-all", "eth_abi",
             "--collect-all", "eth_keys", "--collect-all", "eth_utils",
             "--collect-all", "Crypto", "--collect-all", "ckzg"]
    for module in MODULES:
        args += ["--collect-submodules", module]
    args.append(str(ROOT / "scripts" / "desktop_launcher.py"))
    subprocess.run(args, check=True, cwd=ROOT)
    output = ROOT / "artifacts" / "A2N.exe"
    if options.unsigned:
        output.with_name(output.name + '.release.json').unlink(missing_ok=True)
    from a2n_node.release_signing import ReleasePublisher
    import time
    release = None if options.unsigned else ReleasePublisher(ROOT/'data/release-publisher').sign(output,sequence=time.time_ns())
    if source_hashes(release_files()) != hashes:
        raise RuntimeError('Sources changed during compilation: rebuild before release')
    report = {"artifact": output.name, "bytes": output.stat().st_size,
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "runtime_bundled": True,
        "platform": "Windows", "version": version, "source_sha256": source_digest(hashes),
        "file_sha256": hashes, "packages": list(MODULES), "publisher_signature": {"kind":"ED25519_PROJECT_PUBLISHER","author_did":release['author_did'],"manifest":"A2N.exe.release.json"} if release else None,
        "notice": "完整单文件安装产品，项目发布者离线签名；系统发行证书和公开分发渠道另行配置。"}
    (ROOT / "artifacts" / "A2N-desktop.manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != 'file_sha256'}, ensure_ascii=False))


if __name__ == "__main__":
    main()
