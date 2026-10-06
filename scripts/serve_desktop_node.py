"""Desktop launcher used by network startup and the existing scheduled task."""
import json
from pathlib import Path
import os
import subprocess
import sys

from a2n_node.desktop import main as source_main
from a2n_node.product_cli import default_home, runtime_command


def main():
    home = default_home()
    install_file = home / "installation.json"
    if install_file.exists():
        installation = json.loads(install_file.read_text(encoding="utf-8-sig"))
        binary = Path(installation.get("executable") or "")
        if installation.get("runtime_bundled") and binary.is_file() and binary.name.lower() == "a2n.exe":
            from a2n_node.upgrades import file_digest
            if file_digest(binary) != installation.get("sha256"):
                raise ValueError("Installed desktop SDK changed; not started")
            args = sys.argv[1:]
            if "--home" not in args:
                args = [*args, "--home", str(home)]
            return subprocess.call(runtime_command("a2n_node.desktop", args, executable=str(binary)),
                env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    return source_main(sys.argv[1:])

if __name__ == '__main__':
    raise SystemExit(main())
