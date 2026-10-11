"""Reject release tampering and text-line-ending drift without touching real artifacts."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import pytest


spec = importlib.util.spec_from_file_location('source_release', Path(__file__).resolve().parents[1] / 'scripts/build_sdk_bundle.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


def test_same_sources_with_windows_and_posix_line_endings_have_same_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, 'ROOT', tmp_path)
    path = tmp_path / 'module.py'
    path.write_bytes(b'print(1)\r\nprint(2)\r\n')
    first = bundle.source_hashes([path])
    path.write_bytes(b'print(1)\nprint(2)\n')
    assert bundle.source_hashes([path]) == first
    path.write_bytes(b'print(3)\nprint(2)\n')
    assert bundle.source_hashes([path]) != first


def test_archive_verifies_contents_not_only_sidecar_checksum(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, 'ROOT', tmp_path)
    (tmp_path / 'VERSION').write_text('0.2.0b1')
    output = tmp_path / 'bundle.tar.gz'
    expected = {'good.py':hashlib.sha256(b'good').hexdigest()}
    with tarfile.open(output, 'w:gz') as archive:
        info=tarfile.TarInfo('good.py'); info.size=4; archive.addfile(info, io.BytesIO(b'evil'))
    report={'version':'0.2.0b1','file_sha256':expected,'source_sha256':bundle.source_digest(expected),
            'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'files':1,'bytes':output.stat().st_size}
    output.with_suffix('.manifest.json').write_text(json.dumps(report))
    with pytest.raises(ValueError, match='contents'):
        bundle.check(output, expected)
    report['version']='0.1.0'; output.with_suffix('.manifest.json').write_text(json.dumps(report))
    with pytest.raises(ValueError, match='version'):
        bundle.check(output, expected)
