"""Product process protection; libraries do not change their host's limits."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
import os
import threading

DEFAULT_LIMIT = 4 * 1024**3
_lock = threading.Lock()
_handle = None
_limit = None


class _IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
        'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class _BasicLimits(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
        ('LimitFlags', wt.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
        ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wt.DWORD),
        ('Affinity', ctypes.c_size_t), ('PriorityClass', wt.DWORD), ('SchedulingClass', wt.DWORD)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', _BasicLimits), ('IoInfo', _IOCounters),
        ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
        ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]


def status():
    applied = os.name == 'nt' and _handle is not None
    return {'platform': os.name, 'applied': applied,
        'process_bytes': _limit if applied else None, 'process_tree_bytes': _limit if applied else None,
        'kill_on_launcher_exit': False}


def ensure_limit(limit=DEFAULT_LIMIT):
    """Bound Windows product trees, preserving children through upgrade handoff.

    The job remains attached while member processes exist, even after the last
    user handle closes. KILL_ON_JOB_CLOSE would kill the replacement executable
    when its old launcher exits, so it is deliberately absent.
    """
    global _handle, _limit
    if os.name != 'nt':
        return status()
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError('PROCESS_LIMIT_INVALID')
    with _lock:
        if _handle is not None:
            if limit != _limit:
                raise ValueError('PROCESS_LIMIT_ALREADY_CONFIGURED')
            return status()
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
        api.CreateJobObjectW.restype = wt.HANDLE
        api.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        api.SetInformationJobObject.restype = wt.BOOL
        api.GetCurrentProcess.argtypes = []
        api.GetCurrentProcess.restype = wt.HANDLE
        api.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
        api.AssignProcessToJobObject.restype = wt.BOOL
        api.CloseHandle.argtypes = [wt.HANDLE]
        api.CloseHandle.restype = wt.BOOL
        job = api.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = 0x100 | 0x200  # process AND aggregate job memory
        info.ProcessMemoryLimit = info.JobMemoryLimit = limit
        if not api.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            error = ctypes.get_last_error()
            api.CloseHandle(job)
            raise ctypes.WinError(error)
        if not api.AssignProcessToJobObject(job, api.GetCurrentProcess()):
            error = ctypes.get_last_error()
            api.CloseHandle(job)
            raise ctypes.WinError(error)
        _handle, _limit = job, limit
        return status()
