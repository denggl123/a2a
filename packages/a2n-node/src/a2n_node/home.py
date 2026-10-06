"""Node directories; each Daemon explicitly owns its encrypted runtime.db."""
import os
from pathlib import Path
def same_path(a, b):
    return os.path.normcase(str(Path(a).expanduser().resolve())) == os.path.normcase(str(Path(b).expanduser().resolve()))
def use_home(home, *, force=False):
    path = Path(home).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path
def db_path_of(home):
    return str(Path(home).expanduser().resolve() / "runtime.db")


def acquire_home_lock(home):
    path = Path(home).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    lock_path = path / "runtime.lock"
    handle = lock_path.open("a+b")
    handle.seek(0, 2)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError("这个节点目录已经有运行中的实例") from None
    return handle


def wait_for_home_lock(home, timeout=10):
    """Wait only during an explicitly requested stopped-node transition."""
    import time
    deadline = time.monotonic() + timeout
    while True:
        try:
            return acquire_home_lock(home)
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.1)
