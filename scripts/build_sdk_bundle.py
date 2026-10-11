"""Build a source release for installing the complete SDK on an ordinary server."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ('a2n-kernel', 'a2n-p2p', 'a2n-acceptance', 'a2n-sdk', 'a2n-node')

def release_files():
    files = [ROOT / name for name in ('README.md', 'requirements.txt', 'scripts/bootstrap.py',
             'scripts/install_server.py', 'scripts/serve_public_node.py', 'docs/SDK-ARCHITECTURE.md',
             'docs/SERVER-DEPLOYMENT.md', 'docs/VISION-BUSINESS-DESIGN.md',
             'docs/VISION-ARCHITECTURE-DESIGN.md', 'docs/VISION-IMPLEMENTATION.md', 'docs/X402.md',
             'docs/PAYMENT-COORDINATION.md', 'docs/A2N-points-exchange-design.md', 'docs/POINTS.md',
             'docs/DISCOVERY-FILTERS.md', 'install.sh', 'scripts/sign_release.py',
             'docs/VISION-COMPLETION-WORK.md', 'docs/PERSONALIZED-AGENT-SELECTION.md',
             'docs/SELECTION-MODULE.md', 'docs/TASK-QUALITY-CALIBRATION.md',
             'docs/WORKFLOW-MEDIA-MAINTENANCE.md','docs/LOCAL-PAYMENT-TESTING.md',
             'docker/Dockerfile','docker/acceptance.yaml','docker/network.yaml','docker/workflows.yaml',
             'docker/Dockerfile.utilities','docker/Dockerfile.payment-test','docker/testchain.yaml',
             'examples/utilities/agent.py','examples/payments/anvil_facilitator.py',
             'tests/fixtures/PaymentTestToken.json','tests/fixtures/PaymentTestToken.sol')]
    for package in PACKAGES:
        directory = ROOT / 'packages' / package
        files.append(directory / 'pyproject.toml')
        if (directory / 'README.md').exists():
            files.append(directory / 'README.md')
        files += [p for p in (directory / 'src').rglob('*') if p.is_file()
                  and '__pycache__' not in p.parts and not any(s.endswith('.egg-info') for s in p.parts)
                  and p.suffix not in {'.pyc', '.pyo'}]
    files += [ROOT / name for name in ('VERSION', 'LICENSE', 'NOTICE', 'CONTRIBUTING.md', 'SECURITY.md', 'CHANGELOG.md')]
    files += list((ROOT / 'docs').rglob('*.md'))
    files += [p for p in (ROOT / 'scripts').iterdir() if p.is_file() and p.suffix in {'.py', '.js', '.sh', '.cmd', '.bat', '.ps1'}]
    files += [ROOT / 'packages' / package / 'LICENSE' for package in PACKAGES]
    files += [ROOT / name for name in ('requirements-test-payment.txt', 'install.bat', 'start-sdk.bat',
                                      'start-network.bat', '.gitattributes', '.gitignore', 'pyproject.toml',
                                      '.dockerignore', 'docker/.env.example')]
    for directory in ('tests', 'examples', 'docker', '.github'):
        files += [p for p in (ROOT / directory).rglob('*') if p.is_file()
                  and p.suffix in {'.py', '.md', '.json', '.sol', '.yaml', '.yml'}
                  and '__pycache__' not in p.parts]
    return sorted(set(files))


def source_bytes(path):
    raw = path.read_bytes()
    if path.suffix in {'.py', '.md', '.toml', '.js', '.html', '.sh', '.json', '.yaml', '.yml', '.txt', '.sol', '.ps1'} or path.name in {'VERSION', 'LICENSE', 'NOTICE'}:
        return raw.replace(b'\r\n', b'\n')
    if path.suffix in {'.bat', '.cmd'}:
        return raw.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
    return raw


def source_hashes(files):
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(source_bytes(path)).hexdigest()
            for path in files}


def source_digest(hashes):
    return hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def check(output, hashes):
    report = json.loads(output.with_suffix('.manifest.json').read_text(encoding='utf-8'))
    if report.get('version') != (ROOT / 'VERSION').read_text().strip():
        raise ValueError('Release product version changed: rebuild the SDK bundle')
    if report.get('file_sha256') != hashes or report.get('source_sha256') != source_digest(hashes):
        raise ValueError('Release sources changed: rebuild the SDK bundle before deployment')
    if report['sha256'] != hashlib.sha256(output.read_bytes()).hexdigest():
        raise ValueError('SDK archive checksum does not match its manifest')
    archived = {}
    with tarfile.open(output, 'r:gz') as archive:
        for member in archive.getmembers():
            if not member.isfile() or member.name in archived:
                raise ValueError('Unexpected or duplicate archive member')
            archived[member.name] = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
    if archived != hashes or report['files'] != len(hashes) or report['bytes'] != output.stat().st_size:
        raise ValueError('SDK archive contents do not match the current sources')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Reject an outdated or damaged release bundle')
    args = parser.parse_args(argv)
    output = ROOT / 'artifacts/a2n-sdk-server.tar.gz'
    files = release_files()
    hashes = source_hashes(files)
    if args.check:
        try:
            report = check(output, hashes)
        except (OSError, ValueError, KeyError, tarfile.TarError) as exc:
            parser.exit(1, f'Bundle check failed: {exc}\n')
        print(json.dumps({'ok': True, 'files': report['files'], 'sha256': report['sha256'],
                          'source_sha256': report['source_sha256']}))
        return
    output.parent.mkdir(exist_ok=True)
    import io
    with output.open('wb') as target, gzip.GzipFile(fileobj=target, filename='', mode='wb', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w') as archive:
            for path in files:
                raw = source_bytes(path)
                info = tarfile.TarInfo(path.relative_to(ROOT).as_posix())
                info.size = len(raw)
                info.mode = 0o755 if path.suffix == '.sh' else 0o644
                archive.addfile(info, io.BytesIO(raw))
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                            text=True, capture_output=True, check=True).stdout.strip()
    status = subprocess.run(['git', 'status', '--porcelain', '--', *hashes], cwd=ROOT,
                            text=True, capture_output=True, check=True).stdout
    report = {'artifact': output.name, 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
              'version': (ROOT / 'VERSION').read_text().strip(), 'source_format': 'canonical-lf-text-crlf-batch',
              'packages': list(PACKAGES), 'files': len(files), 'bytes': output.stat().st_size,
              'git_commit': commit, 'source_modified': bool(status),
              'source_sha256': source_digest(hashes), 'file_sha256': hashes}
    output.with_suffix('.manifest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    check(output, hashes)
    print(json.dumps({key: value for key, value in report.items() if key != 'file_sha256'}))

if __name__ == '__main__':
    main()
