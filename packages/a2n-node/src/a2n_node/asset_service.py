"""Signed private file ranges and independently encoded public thumbnails."""
from __future__ import annotations

import base64
import hashlib
import io
import threading
import time
import warnings

from a2n_sdk.assets import AssetBook, CHUNK, valid_ref
from a2n_sdk.experience import signed
from a2n_sdk.trade_facts import delivered

from .feedback_identity import signer_for
from .metadata_mailbox import verified
from .peer import ReplayGuard
from .secure_metadata import PrivateInbox
from .resolution_gateway import envelope as resolution_envelope

VERSION = "a2n-assets/1"
ASSET_DOMAIN = b"a2n-private-asset/1"


def envelope(identity, op, target, body, request_id=None):
    core = resolution_envelope(identity, op, target, body, request_id)
    core.pop("proof", None)
    core["v"] = VERSION
    return signed(core, signer_for(identity))


class AssetService:
    def __init__(self, store, identity, facts, network):
        self.book = AssetBook(store, node_did=identity.did)
        self.identity, self.facts, self.network = identity, facts, network
        self.inbox = PrivateInbox(store, identity.did, domain=ASSET_DOMAIN, name="assets", max_size=100000)
        self.guard, self._lock, self._rates = ReplayGuard(), threading.Lock(), []
        self._uploads = threading.BoundedSemaphore(2)
        self._downloads = threading.BoundedSemaphore(2)
        from .media_inspection import MediaInspector
        self.media=MediaInspector(store,self)

    def upload(self, size, mime_type, chunks):
        if not self._uploads.acquire(blocking=False):
            raise ValueError("ASSET_UPLOAD_BUSY")
        try:
            return self.book.upload(size, mime_type, chunks)
        finally:
            self._uploads.release()

    def record_delivery(self, facts, request, outcome):
        if facts.get("execution") != "DELIVERED" or not isinstance(outcome.result, dict):
            return
        refs = outcome.result.get("assets")
        if not isinstance(refs, list):
            return
        previews = [];documents=[]
        for ref in refs[:8]:
            if not self.book.owned(ref):
                continue
            if facts.get("relation_verified") and facts.get("buyer_did") != self.identity.did:
                self.book.grant(ref, facts["trade_uid"], facts["buyer_did"])
            # 样品一定公开（用户裁决 2026-10-06），所以这里不再等"双方安全声明"。
            # 仍然受限的是**公开预览的来源**：只放本节点有界图像编码器生成的缩略图，
            # 原始文件本身留在私有交付里（`a2n-assets/1` 转送，不进公开页面）。
            if len(previews) < 2:
                preview = self.thumbnail(ref)
                if preview:
                    previews.append(preview)
            if ref['mime_type'] not in {'image/png','image/jpeg','image/webp'}:
                # Inspection can execute a bounded parser. Queue it inside this
                # transaction, then do the expensive work outside ledger locks.
                key=facts['trade_uid']
                if not self.book.store.get('asset_preview_jobs',key):
                    self.book.store.put('asset_preview_jobs',key,{'state':'PENDING','refs':[r for r in refs[:8] if self.book.owned(r)],'created_at':time.time()})
        if previews:
            outcome.metadata["sample_media"] = previews
        if documents:outcome.metadata['sample_documents']=documents

    def drain_previews(self,limit=1):
        from .media_inspection import DRIVER
        from a2n_sdk.trade_facts import digest
        parser=digest([DRIVER,self.book.store.get('media_settings','current',{})])
        pending=[(k,r) for k,r in self.book.store.items('asset_preview_jobs').items() if r['state']=='PENDING'
                 or r.get('error_types') and r.get('parser_digest')!=parser]
        for key,row in sorted(pending,key=lambda item:item[1]['created_at'])[:limit]:
            previews=[];documents=[];errors=[]
            for ref in row['refs']:
                if ref['mime_type'] in {'image/png','image/jpeg','image/webp'}:continue
                try:
                    inspected=self.media.inspect(ref)
                    if len(previews)<2:
                        preview=self.media.thumbnail(inspected)
                        if preview:previews.append(preview)
                    if inspected.get('text') and len(documents)<2:documents.append({'mime_type':'text/plain','text':inspected['text']})
                except Exception as exc:errors.append(type(exc).__name__)
            self.book.store.put('asset_previews',key,{'media_preview':previews,'document_previews':documents})
            self.book.store.put('asset_preview_jobs',key,{**row,'state':'READY','error_types':errors,'parser_digest':parser})

    def public_preview(self,scope,task_id):
        facts=self.facts.for_call(scope,task_id)
        if not facts or facts.get('execution')!='DELIVERED' or facts.get('role')!='provider':return {}
        return self.book.store.get('asset_previews',facts['trade_uid'],{})

    def thumbnail(self, ref):
        if ref["mime_type"] not in {"image/png", "image/jpeg", "image/webp"} or ref["size"] > 2 * 1024 * 1024:
            return None
        try:
            from PIL import Image
            raw = b"".join(self.book.chunks(ref["asset_id"]))
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as image:
                    if image.width * image.height > 10000000 or image.format not in {"PNG", "JPEG", "WEBP"}:
                        return None
                    image.load()
                    # A fresh RGB image carries no EXIF, text chunks, profile or animation.
                    clean = Image.new("RGB", image.size)
                    clean.paste(image.convert("RGB"))
                    for bound in (192, 96, 48):
                        clean.thumbnail((bound, bound))
                        output = io.BytesIO()
                        clean.save(output, format="PNG")
                        data = output.getvalue()
                        if len(data) <= 8192:
                            return {"mime_type": "image/png", "base64": base64.b64encode(data).decode(),
                                    "sha256": hashlib.sha256(data).hexdigest(), "width": clean.width, "height": clean.height}
        except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            return None

    def handle(self, action, request):
        if action != "read":
            return 404, {"code": "UNSUPPORTED_OPERATION"}
        if not verified(request, target=self.identity.did, version=VERSION) or request["op"] != "READ_RANGE":
            return 401, {"code": "UNVERIFIED_IDENTITY"}
        def reply(body, error=False):
            return envelope(self.identity, "ERROR" if error else "RESULT", request["author_did"], body, request["request_id"])
        ok, _ = self.guard.check(request["request_id"], request["issued_at"])
        if not ok:
            return 409, reply({"code": "REPLAYED_REQUEST"}, True)
        with self._lock:
            now = time.monotonic()
            self._rates = [(at, did) for at, did in self._rates if now - at < 60]
            if len(self._rates) >= 512 or sum(did == request["author_did"] for _, did in self._rates) >= 256:
                return 429, reply({"code": "RATE_LIMITED"}, True)
            self._rates.append((now, request["author_did"]))
        try:
            body = request["body"]
            asset_id, uid, start, end = (body[k] for k in ("asset_id", "trade_uid", "start", "end"))
            if not isinstance(asset_id, str) or not isinstance(uid, str) or len(asset_id) > 64 or len(uid) > 80:
                raise ValueError("INVALID_ASSET_IDENTITY")
            if not self.book.authorized(asset_id, uid, request["author_did"]):
                return 403, reply({"code": "ASSET_ACCESS_DENIED"}, True)
            if type(start) is not int or type(end) is not int or not 0 <= end - start < CHUNK:
                raise ValueError("ASSET_RANGE_LIMIT")
            data = b"".join(self.book.chunks(asset_id, start, end))
            private = {"author_did": self.identity.did, "target_did": request["author_did"],
                       "ref": self.book.ref(asset_id), "start": start, "end": end,
                       "base64": base64.b64encode(data).decode(), "range_sha256": hashlib.sha256(data).hexdigest()}
            sealed = PrivateInbox.seal(private, body["recipient_key"], domain=ASSET_DOMAIN, max_size=100000)
            return 200, reply({"sealed_message": sealed})
        except (ValueError, KeyError, TypeError):
            return 400, reply({"code": "INVALID_ASSET_REQUEST"}, True)

    def fetch(self, trade_uid, asset_id, *, timeout_seconds=120):
        facts = self.facts.get(trade_uid)
        if not facts or facts.get("role") != "buyer" or not facts.get("relation_verified") or facts.get("execution") != "DELIVERED":
            raise ValueError("VERIFIED_DELIVERED_TRADE_REQUIRED")
        row = self.book.store.task(facts["scope"], facts["task_id"])
        outcome = (row or {}).get("outcome") or {}
        refs = (outcome.get("result") or {}).get("assets") if isinstance(outcome.get("result"), dict) else None
        ref = next((r for r in refs or [] if valid_ref(r) and r["asset_id"] == asset_id), None)
        if not ref or ref["owner_did"] != facts["provider_did"] or not delivered(outcome):
            raise ValueError("ASSET_NOT_IN_DELIVERY")
        cached = next((r["ref"] for r in self.book.store.items("assets").values() if r.get("state") == "READY" and r.get("origin") == ref), None)
        if cached:
            return cached
        endpoint = facts.get("counterparty_endpoint", "")
        if not self._downloads.acquire(blocking=False):
            raise ValueError("ASSET_DOWNLOAD_BUSY")
        try:
            deadline = time.monotonic() + min(120,max(1,timeout_seconds))
            route = {}
            def timeout(maximum):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("ASSET_DOWNLOAD_DEADLINE")
                return min(remaining, maximum)

            def request_range(body):
                from .asset_mailbox import verify_lease, VERSION as MAILBOX_VERSION, PREFIX as MAILBOX_PREFIX
                from .metadata_mailbox import exchange
                def fresh():
                    return envelope(self.identity, "READ_RANGE", facts["provider_did"], body)
                if not route and endpoint and "/relay/" not in endpoint:
                    request = fresh()
                    try:
                        response, _, status = self.network._request(endpoint.rstrip("/") + "/public/v1/assets/read",
                            request, timeout=timeout(1.5), cap=140000)
                        route["direct"] = True
                        return response, status, request
                    except (ValueError, OSError, PermissionError):
                        pass
                if route.get("direct"):
                    request = fresh()
                    response, _, status = self.network._request(endpoint.rstrip("/") + "/public/v1/assets/read",
                        request, timeout=timeout(3), cap=140000)
                    return response, status, request
                relay = route.get("relay")
                if not relay or route["lease"]["expires_at"] <= time.time() + 3:
                    candidates = [relay] if relay else []
                    for base in list(self.network.root_provider())[:2] if not relay else []:
                        try:
                            session, _ = self.network.handshake({"endpoint": base.rstrip("/") + "/public/v1/coord"}, 16384, timeout(1))
                            candidates.append({"endpoint": base.rstrip("/") + MAILBOX_PREFIX.rstrip("/"), "relay_did": session["node_did"]})
                        except (ValueError, OSError, PermissionError):
                            continue
                    for candidate in candidates:
                        try:
                            answer, _ = exchange(self.network, self.identity, candidate, "lookup", {"target_did": facts["provider_did"]},
                                version=MAILBOX_VERSION, timeout=timeout(1))
                            lease = answer.get("lease")
                            if (not verify_lease(lease) or lease["author_did"] != facts["provider_did"]
                                or lease["relay_did"] != candidate["relay_did"] or lease["endpoint"] != candidate["endpoint"]):
                                raise ValueError("UNVERIFIED_ASSET_LEASE")
                            route.update(relay=candidate, lease=lease)
                            relay = candidate
                            break
                        except (ValueError, OSError, PermissionError, KeyError, TypeError):
                            continue
                    else:
                        raise ValueError("ASSET_ROUTE_UNAVAILABLE")
                # A fresh request id also avoids retrying a direct request whose
                # encrypted answer was lost. Ranges are reads, never Agent calls.
                request = fresh()
                answer, _ = exchange(self.network, self.identity, relay, "forward", {"request": request},
                    version=MAILBOX_VERSION, timeout=timeout(3))
                if answer.get("domain") != VERSION:
                    raise ValueError("ASSET_DOMAIN_MISMATCH")
                return answer["response"], answer["status"], request

            def chunks():
                for start in range(0, ref["size"], CHUNK):
                    end = min(ref["size"] - 1, start + CHUNK - 1)
                    response, status, request = request_range({"trade_uid": trade_uid, "asset_id": asset_id,
                        "start": start, "end": end, "recipient_key": self.inbox.public_key})
                    if (status != 200 or not verified(response, target=self.identity.did, version=VERSION)
                        or response["author_did"] != facts["provider_did"] or response["request_id"] != request["request_id"] or response["op"] != "RESULT"):
                        raise ValueError("UNVERIFIED_ASSET_RESPONSE")
                    body = self.inbox.open(response)
                    data = base64.b64decode(body["base64"], validate=True)
                    if (body.get("ref") != ref or body.get("start") != start or body.get("end") != end
                        or len(data) != end - start + 1 or hashlib.sha256(data).hexdigest() != body.get("range_sha256")):
                        raise ValueError("ASSET_RANGE_DIGEST_MISMATCH")
                    yield data
            return self.book.upload(ref["size"], ref["mime_type"], chunks(), origin=ref)
        finally:
            self._downloads.release()
