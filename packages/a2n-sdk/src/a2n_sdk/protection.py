"""Windows user-bound DPAPI adapter. No plaintext fallback.

https://learn.microsoft.com/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata
Other environments may inject their own Protector into LocalStore.
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


class WindowsProtector:
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("此系统需要配置 Protector；Windows 默认使用系统账户加密")
        self._api = ctypes.WinDLL("crypt32", use_last_error=True)
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel.LocalFree.restype = ctypes.c_void_p

    def _convert(self, raw: bytes, encrypt: bool) -> bytes:
        class Blob(ctypes.Structure):
            _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

        buffer = ctypes.create_string_buffer(raw)
        source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        result = Blob()
        fn = self._api.CryptProtectData if encrypt else self._api.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        fn.restype = wintypes.BOOL
        # CRYPTPROTECT_UI_FORBIDDEN; no LOCAL_MACHINE flag, bound to the current user.
        if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(result.data, result.length)
        finally:
            self._kernel.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))

    def seal(self, raw: bytes) -> bytes:
        return self._convert(raw, True)

    def open(self, sealed: bytes) -> bytes:
        return self._convert(sealed, False)
