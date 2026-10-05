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
