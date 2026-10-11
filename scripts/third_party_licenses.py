"""Collect distribution notices for the runtime and its Windows build toolchain."""
from __future__ import annotations
import importlib.metadata as metadata
import json
from pathlib import Path
import tempfile
import sys
import zipfile
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def collect(root):
    destination = Path(tempfile.mkdtemp(prefix='licenses-', dir=root / '.tmp'))
    pending = ['cryptography', 'wasmtime', 'Pillow', 'eth-account', 'PyInstaller']
    distributions = {}
    while pending:
        name = canonicalize_name(pending.pop())
        if name in distributions:
            continue
        dist = metadata.distribution(name)
        distributions[name] = dist
        for raw in dist.requires or []:
            req = Requirement(raw)
            if not req.marker or req.marker.evaluate():
                pending.append(req.name)
    index = []
    runtime_license = next((path for name in ('LICENSE.txt', 'LICENSE')
                            if (path := Path(sys.base_prefix) / name).is_file()), None)
    if runtime_license is None:
        raise RuntimeError('CPython runtime license is missing from the build environment')
    (destination / 'cpython').mkdir()
    (destination / 'cpython/LICENSE.txt').write_bytes(runtime_license.read_bytes())
    index.append({'name':'CPython', 'version':sys.version.split()[0], 'license':'PSF-2.0',
                  'notices':['cpython/LICENSE.txt']})
    for name, dist in sorted(distributions.items()):
        notices = []
        for file in dist.files or []:
            if any(part.upper().startswith(('LICENSE', 'COPYING', 'NOTICE')) for part in file.parts):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    target = destination / name / str(file).replace('..', '_')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.read_bytes())
                    notices.append(target.relative_to(destination).as_posix())
        index.append({'name': dist.metadata['Name'], 'version':dist.version,
                      'license':dist.metadata.get('License-Expression') or dist.metadata.get('License'),
                      'notices':notices})
    (destination / 'index.json').write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding='utf-8')
    (destination / 'README.txt').write_text(
        'Distribution notices for runtime dependency closure and Windows build toolchain.\n'
        'The list includes build-time packages; it does not imply every listed package is executed.\n'
        'Original license conditions and embedded third-party notices remain applicable.\n', encoding='utf-8')
    with zipfile.ZipFile(root / 'artifacts/THIRD-PARTY-LICENSES.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(destination.rglob('*')):
            if path.is_file():
                info = zipfile.ZipInfo(path.relative_to(destination).as_posix())
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())
    return destination
