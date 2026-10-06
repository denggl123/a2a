"""Verify the process serving the canonical home, without exposing credentials."""
import json
from pathlib import Path
import sys

from a2n_node.protection import system_protector
from a2n_node.upgrades import verify_running_release, file_digest
from a2n_node.product_cli import health, start_background
from a2n_node.home import wait_for_home_lock
from a2n_sdk.client import NodeClient
from a2n_sdk.storage import LocalStore


def client(home):
    store = LocalStore(home / "runtime.db", system_protector())
    try:
        token = store.get("runtime_control", "access")["token"]
    finally:
        store.close()
    return NodeClient("http://127.0.0.1:8771", token=token)


def main():
    home, digest = Path(sys.argv[1]).resolve(), sys.argv[2]
    activate = sys.argv[3:] == ["--activate"]
    for attempt in range(3 if activate else 1):
        api = client(home)
        runtime = api.request("GET", "/v1/runtime")
        try:
            verify_running_release(runtime, digest)
        except ValueError:
            if not activate or attempt == 2:
                raise
            installation = json.loads((home / "installation.json").read_text(encoding="utf-8-sig"))
            binary = installation["executable"]
            if not health(8771, home) or installation.get("sha256") != digest or file_digest(binary) != digest:
                raise ValueError("UNVERIFIED_ACTIVATION_TARGET")
            print("A waiting old supervisor resumed; handing over the same node home.")
            api.request("POST", "/v1/node/stop", {})
            with wait_for_home_lock(home, 20):
                pass
            start_background(home, 8771, [], executable=binary)
            continue
        print(json.dumps({"verified": True, "node_did": runtime["node_did"], **runtime["product_runtime"]}))
        return


if __name__ == "__main__":
    main()
