"""Validate and atomically snapshot the local payment facilitator state."""
import argparse
import json
from pathlib import Path
import subprocess

from a2n_node.product_cli import default_home
from snapshot_io import publish, validated_json


def snapshot_facilitator(directory, container='a2n-payment-testfacilitator', keep=5):
    result = subprocess.run(['docker', 'exec', container, 'cat', '/state/public-test-state.json'],
        capture_output=True, timeout=30, check=True)
    value = validated_json(result.stdout)
    if not isinstance(value.get('token'), str) or not isinstance(value.get('receipts'), dict):
        raise ValueError('FACILITATOR_SNAPSHOT_SCHEMA_INVALID')
    return publish(directory, 'facilitator', result.stdout, keep=keep)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container', default='a2n-payment-testfacilitator')
    parser.add_argument('--backup-root', type=Path, default=default_home() / 'snapshots')
    parser.add_argument('--keep', type=int, default=5)
    args = parser.parse_args(argv)
    output = snapshot_facilitator(args.backup_root / 'facilitator', args.container, args.keep)
    print(json.dumps({'ok': True, 'snapshot': str(output), 'kind': 'LOCAL_TEST_FACILITATOR'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
