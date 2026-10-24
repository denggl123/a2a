<!-- source: dce4b78410b0 -->
# Installable workflows, deliverables and local maintenance

## Module boundaries

Business still shares the existing supply, projection, call, sample, review, dispute and payment ledgers. The discovery coordination layer is responsible for finding nodes and supply; natural-language condition parsing, keyword matching, task categories and selection effectiveness all sit in the local display and evaluation layer. Organising conditions does not execute discovery or an Agent.

The evaluation layer only calls the assembled `ReviewerPort`. The node supplies the code case, semantic interface and actual deliverable specification adaptation; the quality algorithm is not allowed to call payment or public discovery directly. Human experience and assistant acceptance are kept separate, and remain UNKNOWN when no professional reviewer is available.

Complex previews and reviews run in a separate background worker thread. Private file authorisation, technical delivery and payment facts are saved first, so container parsing does not occupy a transaction ledger. Review revisions and cancellations still use version checks; a failed preview does not erase the delivery.

An original deliverable can be explicitly re-checked under the original rules, keeping the original experience and modification chain, without another call or charge. After a preview parse failure, retry only once when the software or the authorisation-parse dependency changes; it will not spin repeatedly. A failed video thumbnail does not discard already parsed real specifications.

## Installable workflows

The account page supports signing, importing, previewing, authorising, installing and rolling back `a2n-container-workflow/1` manifests for this node. A manifest fixes the publisher, package name, version, immutable image digest, execution command, resources and permissions. The same publisher and package name correspond to a fixed supply ID; no historical version may be replaced. After installation, list it explicitly on the supply page; the original samples and reputation are retained.

Default resource limits: no network, read-only root filesystem, no host file mounts, no extra capabilities, non-privileged user, 32 processes, one CPU, separate temporary space; the manifest limits memory and time. GPU must be declared and authorised; this round has no GPU hardware acceptance. Python, media tools or models should be pre-installed in the image at a fixed digest.

Container standard output (including progress) totals at most 1 MiB; JSON input is at most 1 MiB by default, with a hard ceiling of 16 MiB after a declared increase. Large files should be delivered through the existing private asset interface; the file size limit must not be read as permitting inline container JSON of the same size.

Downloading dependencies needs separate authorisation and becomes a persistent installation task. A failure or an interruption by restart may be retried explicitly; a replaced program is not executed automatically. An offline image ID must be installed on the node machine first, and a remote download requires `repository@sha256:digest`.

Windows uses the installed Docker engine. Container execution for a Docker node is enabled by layering `docker/workflows.yaml`: only the node control process reaches the host engine, and the Agent containers it runs do not get the engine socket. The node control process should be installed as full software authorised by the computer's owner.

## Independent checks and deliverable previews

- Code: the buyer first fixes the function, input and expected result, and the actual code runs in a restricted container; the expected answer stays on the buyer node, which compares the return value.
- Semantic: the computer's owner configures and authorises a content review address and explicitly sends the chosen result and the requirement; the interface is `a2n-quality-evaluator/1` and returns a score from 0–1. There are timeouts, size limits and a ban on redirects. The source is labelled as the configured reviewer and does not impersonate a human review.
- Deliverable: read the actual private file and parse image dimensions, WAV duration and sample rate, text and document content, PDF page count and video specifications. PDF and video need an authorised parsing image.
- Private viewing: after signed retrieval, supports preview of images, audio, video, PDF and text, still checking the transaction relation, authorisation, scope and full hash. Executable formats such as HTML cannot be opened inline.
- Public samples: the first ten free technical deliveries are always public. Original attachments stay in the private delivery; the public page uses a small PNG re-encoded by the node, an audio waveform, a video preview and a redacted text summary. Automatic processing cannot recognise private information in every frame; the mandatory public-notice prompt before a call remains in force.

File capacity is set by the owner: a single file up to 1 GiB, assets up to 64 GiB in total, at most 4096 files. The defaults of 16 MiB / 128 MiB are kept; a reminder is shown when approaching the capacity or when the disk is short. A media check reads at most 8 MiB at a time; beyond that the specification check stays unknown and the full private file delivery is unaffected.

## Maintenance and history

Periodic backups are run by the node itself and do not depend on development tools. They are off by default; the owner sets the recovery passphrase, interval and number of copies, and the passphrase is stored only in the existing encrypted storage. Only backups created by this module are rotated; manual backups, financial, review and transaction history are kept.

Reviews of completed tasks and inactive calibration models can be moved to encrypted archives. Pending tasks and active models are protected; they can still be read by their original ID, and the list supports `archived=true`, `limit` and `offset`. Archiving does not change review data, feedback or payment state.

Release tooling uses a separate, persistent, system-protected project publisher key to produce a manifest verifiable by the existing upgrade protocol. It is separate from an ordinary user's node identity. The Windows build includes the runtime and the five packages; `install.sh` provides the Linux/macOS source installation entry point and requires system Python 3. A native full installer for macOS, notarisation certificates and public distribution channels still need the corresponding environment.

When a release sequence number exceeds the browser's safe integer range, use a canonical decimal string, keeping import, signing and rollback-protection comparisons consistent; historical integer manifests remain compatible.
