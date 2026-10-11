"""Read the selected desktop's resource status without launching or killing it."""
import argparse
import json
import subprocess
import sys

from a2n_node.product_cli import default_home


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', default=str(default_home()))
    parser.add_argument('--port', type=int, default=8771)
    args = parser.parse_args(argv)
    result = subprocess.run([sys.executable, '-m', 'a2n_node.product_cli', 'request',
        '--home', str(args.home), '--port', str(args.port), '--path', '/v1/runtime'],
        check=True, capture_output=True, text=True, encoding='utf-8', timeout=30)
    state = json.loads(result.stdout)
    print(json.dumps({'node_did': state['node_did'], 'product_runtime': state['product_runtime']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
