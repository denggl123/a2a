"""Build a source release for installing the complete SDK on an ordinary server."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ('a2n-kernel', 'a2n-p2p', 'a2n-acceptance', 'a2n-sdk', 'a2n-node')

def main():
    output = ROOT / 'artifacts/a2n-sdk-server.tar.gz'
    output.parent.mkdir(exist_ok=True)
    files = [ROOT / name for name in ('README.md', 'requirements.txt', 'scripts/bootstrap.py',
             'scripts/install_server.py', 'scripts/serve_public_node.py', 'docs/SDK-ARCHITECTURE.md',
             'docs/SERVER-DEPLOYMENT.md')]
    for package in PACKAGES:
        directory = ROOT / 'packages' / package
        files.append(directory / 'pyproject.toml')
        files += [p for p in (directory / 'src').rglob('*') if p.is_file()
                  and '__pycache__' not in p.parts and not any(s.endswith('.egg-info') for s in p.parts)
                  and p.suffix not in {'.pyc', '.pyo'}]
    with tarfile.open(output, 'w:gz') as archive:
        for path in sorted(files):
            archive.add(path, arcname=path.relative_to(ROOT).as_posix(), recursive=False)
    report = {'artifact': output.name, 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
              'packages': list(PACKAGES), 'files': len(files), 'bytes': output.stat().st_size}
    output.with_suffix('.manifest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))

if __name__ == '__main__':
    main()
