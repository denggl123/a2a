"""Install the complete SDK as one persistent Linux systemd node.

Run as root from a dedicated release directory. Requires Python 3.11+, venv,
and systemd. Does not change SSH, nginx, or the host firewall configuration.
"""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ('a2n-kernel', 'a2n-p2p', 'a2n-acceptance', 'a2n-sdk', 'a2n-node')

def run(argv, **kwargs):
    subprocess.run([str(a) for a in argv], check=True, **kwargs)

def main():
    p = argparse.ArgumentParser(description='Install one complete Linux A2N SDK service')
    p.add_argument('--public-base', required=True)
    p.add_argument('--public-port', type=int, default=8891)
    p.add_argument('--public-node', action='append', default=[])
    p.add_argument('--coord-allow-network', action='append', default=[])
    args = p.parse_args()
    if os.name != 'posix' or os.geteuid() != 0:
        p.error('Linux root is required for the service installation')
    if sys.version_info < (3, 11) or not shutil.which('systemctl'):
        p.error('Python 3.11+ and systemd are required')
    base = urlsplit(args.public_base)
    if (base.scheme not in {'http', 'https'} or not base.hostname or base.username
            or base.password or base.path not in {'', '/'} or base.query or base.fragment):
        p.error('public-base must be a plain HTTP(S) origin without credentials')
    if not 1024 <= args.public_port <= 65535 or args.public_port == 8771:
        p.error('public-port must be 1024..65535 and different from local admin port 8771')
    for net in args.coord_allow_network:
        ipaddress.ip_network(net)
    values = [str(ROOT), args.public_base, *args.public_node, *args.coord_allow_network]
    if any(any(c in v for c in ('\n', '\r', '"', '\\', '%', '$')) for v in values):
        p.error('unsupported characters in deployment paths or settings')
    import pwd
    try:
        account = pwd.getpwnam('a2n')
    except KeyError:
        run(['useradd', '--system', '--home-dir', '/var/lib/a2n', '--shell',
             shutil.which('nologin') or '/usr/sbin/nologin', 'a2n'])
        account = pwd.getpwnam('a2n')
    state = Path('/var/lib/a2n')
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(state, account.pw_uid, account.pw_gid)
    config = Path('/etc/a2n')
    config.mkdir(mode=0o700, exist_ok=True)
    env_file = config / 'node.env'
    old = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if '=' in line and not line.startswith('#'):
                name, value = line.split('=', 1)
                old[name] = value.strip('"')
    key = old.get('A2N_STORAGE_KEY') or base64.b64encode(secrets.token_bytes(32)).decode()
    environment = {**old, 'A2N_STORAGE_KEY': key, 'A2N_HOME': '/var/lib/a2n/node',
        'A2N_PORT': '8771', 'A2N_PUBLIC_BASE': args.public_base.rstrip('/'),
        'A2N_PUBLIC_PORT': str(args.public_port), 'A2N_PUBLIC_NODES': ','.join(args.public_node),
        'A2N_COORD_ALLOW_NETWORKS': ','.join(args.coord_allow_network), 'PYTHONUNBUFFERED': '1'}
    temporary = config / 'node.env.new'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(''.join(f'{k}="{v}"\n' for k, v in environment.items()))
    os.chmod(temporary, 0o600)
    os.replace(temporary, env_file)
    venv = ROOT / '.venv'
    if not (venv / 'bin/python').exists():
        run([sys.executable, '-m', 'venv', venv])
    python = venv / 'bin/python'
    run([python, '-m', 'pip', 'install', '--upgrade', 'pip', '-q'])
    run([python, '-m', 'pip', 'install', 'cryptography>=42', '-q'])
    run([python, '-m', 'pip', 'install', '--no-deps', *[ROOT / 'packages' / v for v in PACKAGES], '-q'])
    run([python, '-m', 'a2n_node.product_cli', 'doctor'], env={**os.environ, **environment})
    unit = Path('/etc/systemd/system/a2n-sdk.service')
    if unit.exists():
        shutil.copy2(unit, unit.with_suffix('.service.backup-' + datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')))
    unit.write_text(f'''[Unit]
Description=A2N complete SDK node
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=a2n
Group={account.pw_gid}
WorkingDirectory={ROOT}
EnvironmentFile=/etc/a2n/node.env
ExecStart="{python}" -u "{ROOT / 'scripts/serve_public_node.py'}"
Restart=on-failure
RestartSec=3
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/a2n
UMask=0077

[Install]
WantedBy=multi-user.target
''', encoding='utf-8')
    admin = Path('/usr/local/bin/a2n-admin')
    admin.write_text(f'''#!/bin/sh
set -eu
set -a
. /etc/a2n/node.env
set +a
exec "{python}" -m a2n_node.product_cli request "$@"
''', encoding='utf-8')
    admin.chmod(0o700)
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', 'a2n-sdk.service'])
    run(['systemctl', 'restart', 'a2n-sdk.service'])
    run(['systemctl', 'is-active', 'a2n-sdk.service'])
    print(json.dumps({'service': 'a2n-sdk', 'packages': 5, 'public_base': args.public_base,
                      'admin': 'loopback only: 127.0.0.1:8771', 'data': '/var/lib/a2n/node'}))

if __name__ == '__main__':
    main()
