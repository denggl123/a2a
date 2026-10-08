"""Verify installed Linux settlement, onboarding, recovery and payments in a disposable container."""
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
CODE = '''import subprocess,sys
r=subprocess.run([sys.executable,'-m','pip','install','--disable-pip-version-check','-r','/checks/requirements-test-payment.txt'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
if r.returncode:
    print(r.stdout[-4000:]);sys.exit(r.returncode)
print('Testing installed Linux modules in an isolated disposable container.',flush=True)
sys.exit(subprocess.call([sys.executable,'-m','pytest','-q','tests/test_settlement_transport.py','tests/test_onboarding.py','tests/test_upgrades.py','tests/test_points.py','tests/test_points_market.py','tests/test_payment_coordination.py','tests/test_paid_market.py','tests/test_payment_channel_lifecycle.py','tests/test_public_entry.py','tests/test_sdk_product.py','--junitxml=/reports/completion-docker.xml']))
'''


def main():
    args = ['docker', 'run', '--rm', '--name', 'a2n-completion-module-check',
        '--mount', f'type=bind,source={ROOT / "tests"},target=/checks/tests,readonly',
        '--mount', f'type=bind,source={ROOT / "requirements-test-payment.txt"},target=/checks/requirements-test-payment.txt,readonly',
        '--mount', f'type=bind,source={ROOT / "artifacts"},target=/reports',
        '-w', '/checks', '--entrypoint', 'python', 'a2n-node-test', '-c', CODE]
    result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=420)
    (ROOT / '.tmp/completion-docker-check.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    print((result.stdout + result.stderr)[-4000:])
    if result.returncode:
        return result.returncode
    suite = next(ET.parse(ROOT / 'artifacts/completion-docker.xml').getroot().iter('testsuite'))
    print(json.dumps({'passed': suite.get('failures') == '0' and suite.get('errors') == '0',
        'tests': int(suite.get('tests')), 'skipped': int(suite.get('skipped'))}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
