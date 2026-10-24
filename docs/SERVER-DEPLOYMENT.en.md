<!-- source: dea6747125c7 -->
# Installing the complete SDK as a Linux service

This entry point installs the same 5 packages and the same node runtime. A server runs exactly one SDK node; no central platform is built alongside it.

## The install package

On the development machine run `python scripts/build_sdk_bundle.py` to produce `artifacts/a2n-sdk-server.tar.gz` and a SHA256 manifest. The package contains only the current SDK source and the install entry point, with no local database, node storage key or SSH credential.

Before publishing, run `python scripts/build_sdk_bundle.py --check`, which verifies every file inside the package, the current source and the manifest together. An old package is rejected once the source has changed. The manifest records the Git commit, whether uncommitted changes were present, the overall source hash and each file's hash; an uncommitted fix cannot be identified by commit id alone.

The server needs Python 3.11+, venv and systemd. Unpack the package into its own directory, for example `/opt/a2n/releases/20261005`, and run as root:

```sh
python3 scripts/install_server.py \
  --public-base http://172.245.148.171:8891 \
  --public-port 8891 \
  --coord-allow-network 172.245.148.171/32
```

That HTTP address serves an explicitly authorised pilot network; other nodes likewise need to allow that server's `/32` explicitly. A production public entry point is an HTTPS origin with a valid certificate; an existing nginx can forward the public protocol paths of that origin to 8891.

## Service and data

- Service: `a2n-sdk.service`, running as a dedicated `a2n` user, started at boot and restarted on failure.
- Business data: `/var/lib/a2n/node/runtime.db`, AES-GCM encrypted; an upgrade keeps the identity and the data.
- Key and node parameters: `/etc/a2n/node.env`, root-owned with mode 0600; an existing storage key is reused.
- Management entry point: loopback `127.0.0.1:8771`; the public proxy exposes only the public protocol, so the management API and console are not open on the public port.
- Local management: `a2n-admin --path /v1/runtime`. The wrapper the installer generates hands arguments and stdin unchanged to the unified `a2n_node.product_cli request`; the source build supports `--method POST --body -`, and the CLI takes the management token from the local encrypted database, so the token never has to be printed or pasted into a command.
  The 2026-10-06 deployment record concluded from terse help output and a failed operation that the server supported `--path` only. That conclusion does not follow from help listing only `--home/--port`, because API arguments are handed on to an inner parser. This round verified on a real isolated node that the same wrapper command accepts POST and JSON on stdin, and completed the help text. The server commands could not be re-tested this round because the SSH connection dropped; remote write operations and five-node acceptance still have to be actually run and do not count as passed.
- A complete install resolves the dependencies of the five local packages, including `wasmtime` and `Pillow`; `doctor` really compiles WASM and encodes a PNG. When an execution backend is missing the self-check fails instead of reporting that feature as complete.
- Checks: `systemctl status a2n-sdk`, `journalctl -u a2n-sdk`.

The install entry point does not modify existing SSH, nginx or host firewall rules. During deployment, check that 8891 is reachable and open that public port according to the actual host rules. Deployment succeeds when the service is ready, the identity answers and real cross-node acceptance passes; producing an install package does not mean the server is installed.

## The four-node local entry point

`start-network.bat` starts the three existing Docker nodes plus the local complete SDK. `-HostAddress 192.168.31.21` can be given explicitly; when an existing desktop node uses a different configuration, add `-RestartDesktop` for a normal restart that keeps the identity and the trade records.

Desktop network settings are also saved in `network.json` in the actual node directory, and both the SDK start and the scheduled task read it; `.tmp/network-settings.json` keeps the network launcher's choice. In the current Windows environment the user directory differs between the scheduled task and the current session, so `.a2n-local.json` in the repository points at a shared `data/desktop-sdk`. The scheduled task runs the project Python directly, with no Git Bash involved; a watchdog loop restores the same identity after the node exits. The original user directory and the old node directory are kept, and the old identity must not be started from another entry point.

The local public ports are 18881, 18882, 18883 and 18885, with management staying on loopback. The local discovery ring is `A → B → C → Desktop → A`. Run `python scripts/network_acceptance.py` to verify discovery, a real HTTP supply call and a signed receipt between every two distinct nodes; the result is written to `artifacts/network-local.json`.

This launch configuration uses the current LAN address. When DHCP changes the address, re-run the launcher to update the public address; this LAN configuration still does not prove the server has joined, and cannot replace acceptance across the public internet or NAT.

## Joining the fifth node and accepting it

Once the server is installed and the public port really is reachable, enable the optional sealed task relay through the server's local management interface:

```sh
printf '%s' '{"services":{"task_relay":true}}' | \
  a2n-admin --path /v1/public-services --method POST --body -
```

On the development machine, apply the server entry point:

```powershell
pwsh -File scripts/start_network.ps1 -ServerBase http://172.245.148.171:8891 -RestartDesktop
```

The three Docker nodes and the desktop SDK keep their LAN entry points and discovery ring, and also actively register a coordination mailbox and sealed supply with the server. The HTTP pilot trusts that server's IPv4 `/32` only; an HTTPS domain does not relax certificate verification. The settings are saved in `.tmp/network-settings.json` and the launcher keeps using them afterwards. To go back to the four-node configuration, use `-LocalOnly -RestartDesktop`. The startup message only means the arguments were applied; five nodes being online and interoperating has to be shown by actual acceptance.

For acceptance, also run an HTTP arithmetic upstream on the server's loopback; this test supply is not loaded by an ordinary SDK install. Copy the repository's `tests/docker/agents.py` to the server on its own and start it with `A2N_TEST_AGENT_HOST=127.0.0.1`; the default test port is 9000 and `A2N_TEST_AGENT_PORT` changes it. A real agent on the server can also serve the same `{sum: a+b}` HTTP protocol for this round.

The remote management interface always stays on loopback and is reached over a stable SSH management channel. `--server-admin` is a JSON argument array and the script creates the process directly without going through a local shell; that command must accept the same arguments and JSON on stdin as `a2n-admin`, and write only the API JSON to stdout. For example, with SSH key authentication already in place:

```powershell
$serverAdmin = '["ssh","-o","BatchMode=yes","root@172.245.148.171","/usr/local/bin/a2n-admin"]'
.venv\Scripts\python.exe scripts/network_acceptance.py `
  --server-admin $serverAdmin --server-upstream http://127.0.0.1:9000/invoke
```

This mode checks 5 distinct DIDs, mandatory discovery, listing through the real API, 20 directed buy/sell combinations with receipt signatures, both DIDs and the output hash. When the server calls the four LAN supplies it must pick the sealed relay card. Only when all 75 checks pass is a successful `artifacts/network-five.json` produced; on failure the existing facts, the failing step and the error are kept. Without the server arguments it still runs the 45 four-node checks into `network-local.json`, which do not count as public-network verification.
