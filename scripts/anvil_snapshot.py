"""Create validated local test-chain snapshots without changing the chain."""
import argparse
import json
from pathlib import Path
import urllib.request

from a2n_node.product_cli import default_home
from snapshot_io import MAX_BYTES, chain_json, publish


def dump_state(rpc_url, out_dir, keep=5):
    request = urllib.request.Request(rpc_url,
        data=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'anvil_dumpState', 'params': []}).encode(),
        headers={'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=30) as response:
        wire = response.read(MAX_BYTES * 2 + 4096)
    if len(wire) >= MAX_BYTES * 2 + 4096:
        raise ValueError('SNAPSHOT_RESPONSE_TOO_LARGE')
    reply = json.loads(wire)
    if reply.get('error') or not isinstance(reply.get('result'), str):
        raise ValueError('SNAPSHOT_RPC_FAILED')
    raw = bytes.fromhex(reply['result'].removeprefix('0x'))
    return publish(out_dir, 'testchain', chain_json(raw), compressed=True, keep=keep)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rpc', default='http://127.0.0.1:18945')
    parser.add_argument('--backup-root', type=Path, default=default_home() / 'snapshots')
    parser.add_argument('--keep', type=int, default=5)
    parser.add_argument('--once', action='store_true', help='Compatibility flag; always one round')
    args = parser.parse_args(argv)
    output = dump_state(args.rpc, args.backup_root / 'testchain', args.keep)
    print(json.dumps({'ok': True, 'snapshot': str(output), 'kind': 'LOCAL_TEST_CHAIN'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
