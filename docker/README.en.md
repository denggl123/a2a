<!-- source: 0e136e7c6ec6 -->
# One node is a whole platform

The image starts the same real `Daemon` and encrypted business store. The HTTP Agent and the test chain are support services, not additional A2N nodes. For overall status see [project status](../docs/PROJECT-STATUS.en.md); for which tool does what see [script entry points](../scripts/README.en.md).

## The four local nodes today

The Windows full SDK listens on 8771 for management and 18885 publicly; the Docker `node-a/b/c` listen publicly on 18881/18882/18883, with management kept on each container's loopback. `docker/acceptance.yaml` is the three-node base configuration and `docker/network.yaml` the overlay that joins Windows, the LAN and an optional remote node. These nodes hold real identities and historical data volumes; they are not temporary directories to delete after each test.

To build the dedicated environment the first time: `python scripts/docker_acceptance.py`; to join Windows: the root `start-network.bat`. Restarting an existing network reuses the original `.tmp/network.env` and keeps the storage key:

```powershell
docker compose -f docker/acceptance.yaml -f docker/network.yaml --env-file .tmp/network.env up -d
```

That environment file holds storage secrets and never enters version control. Starting containers is not the same as having upgraded the source; check the current image and the four-node state with `python scripts/project_audit.py --installed`.

### Test Agents and the execution image

`agents` is the acceptance HTTP service (18884) and `business-agent` the real text-inspection Agent (18910). Both use the current `a2n-node-test` image with a read-only code mount and hold no business wallet or ledger. The text Agent already has a Compose profile:

```powershell
docker compose -f docker/acceptance.yaml -f docker/network.yaml --env-file .tmp/network.env --profile business up -d --no-deps agents business-agent
```

When a hand-created text container of the same name moves to Compose for the first time, verify its labels and then replace only that container; the node data volume stays. After upgrading the node image, rebuild these two test services as well, so running containers do not reference a historical image that no longer exists. Installed workflows and independent review may also use a pinned-SHA `a2n-utilities` image, which remains a real dependency even with no resident container.

For the test chain and the facilitator see [local payment testing](../docs/LOCAL-PAYMENT-TESTING.en.md); their persistent volumes have to be kept as a pair. Test services and local test coins prove neither real user acceptance nor that mainnet payment works.

## Ordinary deployment

`docker/compose.yaml` is another deployment template over the same node image, with different node names and ports; it is neither a second business implementation nor the way the current four nodes are started. Set `A2N_STORAGE_KEY` in `docker/.env` and run `docker compose -f docker/compose.yaml --env-file docker/.env up -d --build`. The original three named volumes carry over; the current node restores existing identities and offerings.

Inside Docker, container DNS is used; host ports are for checking the public interface. To reach a real external machine, change `A2N_PUBLIC_BASE` to an HTTPS address both sides can reach. The management API always stays on loopback and is never exposed through a public proxy.

## Real multi-node acceptance

`python scripts/docker_acceptance.py` builds the same image, starts independent nodes A, B and C plus the test HTTP Agent, lists through the node management API and then makes real discovery and trade requests. Results go to `artifacts/docker-acceptance.json` and a Markdown report. Test services load only in the acceptance Compose.

Node A is configured with B only, and B with C only; C's offering is discovered hop by hop through B's referral. Every node has to serve basic discovery and its existing public samples even with nothing on sale; witness and task relay are configured separately. A Docker bridge is not proof of public NAT, cross-carrier reachability or long-term capacity.

Before cleaning up, check containers, signed workflows and review-image dependencies together; identities, ledgers, encrypted backups and recovery material are not rubbish just because nothing has them mounted. Historical reports keep their original dates, and acceptance against installed nodes produces real business facts that cannot stand in for human calibration data.
