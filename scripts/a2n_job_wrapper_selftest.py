"""Test a small memory cap in an isolated child, never the installed node."""
import json
import os
import subprocess
import sys


def main():
    if os.name != 'nt':
        print(json.dumps({'skipped': True, 'reason': 'Windows native job limits'}))
        return 0
    program = """
import ctypes,json
from a2n_node.process_limits import ensure_limit
state=ensure_limit(96*1024**2)
allocated=[]
try:
    for _ in range(20):
        allocated.append(ctypes.create_string_buffer(16*1024**2))
except MemoryError:
    print(json.dumps({'passed':True,'resource_limits':state}))
    raise SystemExit(0)
raise SystemExit(1)
"""
    result = subprocess.run([sys.executable, '-c', program], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('ISOLATED_MEMORY_CAP_FAILED')
    print(result.stdout.strip())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
