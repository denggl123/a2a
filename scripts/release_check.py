"""Verify release metadata, docs and optionally the exact source/desktop artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
from build_sdk_bundle import PACKAGES, source_hashes, release_files, source_digest, check


def check_metadata():
    version = (ROOT / 'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:[ab]\d+)?', version):
        raise ValueError('Invalid canonical VERSION')
    for name in PACKAGES:
        project = tomllib.loads((ROOT / 'packages' / name / 'pyproject.toml').read_text(encoding='utf-8'))['project']
        if project['version'] != version or project.get('license') != 'Apache-2.0':
            raise ValueError('Package version/license mismatch: ' + name)
        if (ROOT / 'packages' / name / 'LICENSE').read_bytes() != (ROOT / 'LICENSE').read_bytes():
            raise ValueError('Distribution license mismatch: ' + name)
    from a2n_sdk.version import __version__
    if __version__ != version:
        raise ValueError('Runtime product version mismatch')
    for filename in ('README.md', 'docs/README.md', 'docs/RELEASING.md', 'docs/INTEGRATIONS.md',
                     'docs/AUTOMATIC-SETTLEMENT.md', 'LICENSE', 'NOTICE', 'CONTRIBUTING.md', 'SECURITY.md', 'CHANGELOG.md'):
        if not (ROOT / filename).is_file():
            raise ValueError('Missing publication document: ' + filename)
    # Enforce live documentation entry points; historical records have their own archive index.
    for filename in ('README.md', 'packages/a2n-sdk/README.md', 'docs/README.md', 'docs/RELEASING.md', 'docs/INTEGRATIONS.md', 'docs/AUTOMATIC-SETTLEMENT.md'):
        path = ROOT / filename
        for target in re.findall(r'\]\(([^)]+)\)', path.read_text(encoding='utf-8')):
            if '://' in target or target.startswith('#'):
                continue
            target = target.split('#', 1)[0].strip('<>')
            if not (path.parent / target).exists():
                raise ValueError(f'Broken documentation link: {filename}: {target}')
    return version


def check_desktop(version, hashes, *, doctor=False, require_signed=False):
    path = ROOT / 'artifacts/A2N.exe'
    report = json.loads((ROOT / 'artifacts/A2N-desktop.manifest.json').read_text(encoding='utf-8'))
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    if (report.get('version') != version or report.get('source_sha256') != source_digest(hashes)
            or report.get('file_sha256') != hashes or report.get('sha256') != checksum
            or report.get('bytes') != path.stat().st_size):
        raise ValueError('Desktop artifact is outdated or its metadata differs from the sources')
    signature = report.get('publisher_signature')
    if require_signed and not signature:
        raise ValueError('Trusted publication requires a persistent publisher signature')
    if signature:
        from a2n_sdk.experience import unsigned
        from a2n_node.feedback_identity import verifier_for
        manifest = json.loads(path.with_name(path.name + '.release.json').read_text(encoding='utf-8'))
        if (manifest['sha256'] != checksum or manifest['size'] != path.stat().st_size
                or manifest['author_did'] != signature['author_did']
                or not verifier_for()(manifest['proof'], unsigned(manifest))):
            raise ValueError('Desktop publisher signature mismatch')
    if doctor:
        output = ROOT / 'artifacts/release-doctor.json'
        subprocess.run([str(path), 'doctor', '--report', str(output)], check=True, timeout=120)
        result = json.loads(output.read_text(encoding='utf-8'))
        runtime = result['runtime']
        if (not result['ok'] or runtime['version'] != version or runtime['source_sha256'] != source_digest(hashes)
                or runtime['executable_sha256'] != checksum or runtime['mode'] != 'BUNDLED'):
            raise ValueError('The executable did not run the expected bundled sources')
    return {'sha256': checksum, 'signed': bool(signature), 'doctor': doctor}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', action='store_true')
    parser.add_argument('--desktop', action='store_true')
    parser.add_argument('--doctor', action='store_true')
    parser.add_argument('--require-signed', action='store_true')
    args = parser.parse_args(argv)
    version = check_metadata()
    hashes = source_hashes(release_files())
    result = {'ok': True, 'version': version, 'packages': len(PACKAGES), 'source_sha256': source_digest(hashes)}
    if args.artifacts:
        result['source_archive'] = check(ROOT / 'artifacts/a2n-sdk-server.tar.gz', hashes)['sha256']
    if args.desktop or args.doctor or args.require_signed:
        result['desktop'] = check_desktop(version, hashes, doctor=args.doctor, require_signed=args.require_signed)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    main()
