"""Bounded, validated, atomic payment-test snapshots; never restore implicitly."""
import gzip
import json
import os
from pathlib import Path
import tempfile
import time

MAX_BYTES = 64 * 1024**2


def validated_json(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError('SNAPSHOT_TOO_LARGE')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('SNAPSHOT_MUST_BE_OBJECT')
    return value


def chain_json(raw):
    if raw.startswith(b'\x1f\x8b'):
        import io
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            raw = stream.read(MAX_BYTES + 1)
    validated_json(raw)
    return raw


def publish(directory, prefix, raw, *, compressed=False, keep=5):
    if not isinstance(keep, int) or not 1 <= keep <= 100:
        raise ValueError('SNAPSHOT_KEEP_INVALID')
    if prefix not in {'testchain', 'facilitator'}:
        raise ValueError('SNAPSHOT_PREFIX_INVALID')
    validated_json(raw)
    directory = Path(directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    suffix = '.json.gz' if compressed else '.json'
    name = prefix + '-' + time.strftime('%Y%m%dT%H%M%S') + '-%019d' % time.time_ns() + suffix
    output = directory / name
    payload = gzip.compress(raw, mtime=0) if compressed else raw
    descriptor, temporary = tempfile.mkstemp(prefix='.' + prefix + '-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
        if os.name != 'nt':
            directory_fd = os.open(directory, os.O_RDONLY)
            try: os.fsync(directory_fd)
            finally: os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)
    snapshots = sorted(directory.glob(prefix + '-*' + suffix), reverse=True)
    for old in snapshots[keep:]:
        if old.is_file() and not old.is_symlink():
            old.unlink()
    return output
