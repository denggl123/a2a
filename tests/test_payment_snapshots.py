"""Reject corrupt snapshots and retain only complete, uniquely named backups."""
import gzip
import importlib.util
import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


def load_io():
    spec = importlib.util.spec_from_file_location('snapshot_io_test', SCRIPTS/'snapshot_io.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_chain_payload_accepts_raw_json_and_validates_full_gzip_crc():
    module = load_io()
    raw = b'{"accounts":{}}'
    assert module.chain_json(raw) == raw
    assert module.chain_json(gzip.compress(raw)) == raw
    with pytest.raises((EOFError, OSError)):
        module.chain_json(gzip.compress(raw)[:-4])
    with pytest.raises(ValueError):
        module.chain_json(b'{broken')


def test_corrupt_input_and_failed_publication_preserve_prior_backup(tmp_path, monkeypatch):
    module = load_io()
    first = module.publish(tmp_path, 'testchain', b'{"block":1}', compressed=True)
    with pytest.raises(ValueError):
        module.publish(tmp_path, 'testchain', b'[]', compressed=True)
    monkeypatch.setattr(module.os, 'replace', lambda *args: (_ for _ in ()).throw(OSError('disk unavailable')))
    with pytest.raises(OSError):
        module.publish(tmp_path, 'testchain', b'{"block":2}', compressed=True)
    assert json.loads(gzip.decompress(first.read_bytes())) == {'block': 1}
    assert len(list(tmp_path.iterdir())) == 1


def test_unique_snapshots_and_bounded_rotation_preserve_other_files(tmp_path):
    module = load_io()
    sentinel = tmp_path/'another-service.json'
    sentinel.write_text('untouched')
    names = [module.publish(tmp_path, 'facilitator', json.dumps({'block': i}).encode(), keep=2) for i in range(5)]
    assert len(set(names)) == 5
    assert {p.name for p in tmp_path.glob('facilitator-*.json')} == {p.name for p in names[-2:]}
    assert sentinel.read_text() == 'untouched'
    with pytest.raises(ValueError):
        module.publish(tmp_path, 'facilitator', b'{}', keep=0)


def test_decompressed_payload_has_bound(monkeypatch):
    module = load_io()
    monkeypatch.setattr(module, 'MAX_BYTES', 32)
    with pytest.raises(ValueError, match='SNAPSHOT_TOO_LARGE'):
        module.chain_json(gzip.compress(b'{"large":"' + b'x'*80 + b'"}'))
