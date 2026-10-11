"""Exercise native DPAPI: repeated storage reads must not retain ctypes types."""
import ctypes
import gc
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from a2n_sdk.protection import WindowsProtector


@pytest.mark.skipif(os.name != "nt", reason="Native Windows DPAPI")
def test_repeated_and_concurrent_decryption_has_bounded_native_type_cache():
    protector = WindowsProtector()
    raw = b"persistent resource regression payload"
    sealed = protector.seal(raw)
    assert protector.open(sealed) == raw
    gc.collect()
    before = len(ctypes._pointer_type_cache)
    for _ in range(1000):
        assert protector.open(sealed) == raw
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert all(value == raw for value in executor.map(protector.open, [sealed] * 100))
    gc.collect()
    assert len(ctypes._pointer_type_cache) == before
