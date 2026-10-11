"""Run real browser acceptance against a new isolated node on an ephemeral port."""
from __future__ import annotations
import argparse
import base64
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', default=os.environ.get('NODE_EXE') or shutil.which('node'))
    args = parser.parse_args(argv)
    if not args.node:
        parser.error('Install Node.js or set NODE_EXE')
    from a2n_node.daemon import Daemon
    from a2n_node.protection import EnvironmentProtector
    (ROOT / '.tmp').mkdir(exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix='console-acceptance-', dir=ROOT / '.tmp'))
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    node = Daemon(output / 'home', port=0, protector=protector, beacon=False).start()
    try:
        env = {**os.environ, 'A2N_NODE_BASE':node.runtime.local_base_url, 'OUT':str(output / 'browser')}
        result = subprocess.run([args.node, str(ROOT / 'scripts/node_console_check.js')],
                                cwd=ROOT, env=env, timeout=240)
        print('Browser output: ' + str(output / 'browser'))
        return result.returncode
    finally:
        node.stop()


if __name__ == '__main__':
    raise SystemExit(main())
