"""自持节点：一个人自己跑的那份网络。

平台模式下，"网络"这个角色由 a2n-server 承担 —— 注册表、门禁、公证、调度都在
一个进程 + 一个库里。把平台拿掉，就缺一个**节点形态的装配体**，本模块就是它：

    身份（a2n-p2p）＋ 发现（gossip）＋ 卡片自证（card）＋ 直连调用（peer）
    ＋ 双边互证（receipt）＋ 本地刻章（a2n-notary）

**无托管模式的三条不变式**（区别不在"有没有"，只在"谁承担"）：

  1. **没有发行方**：没有托管就没有积分增发。节点不产生任何"钱"，只产生事实
     （谁调了谁、干了什么、结果是什么哈希）。于是"结算"这件事在无托管模式下
     不是被省掉了，而是**本就不存在**——没有可记账的钱，也就没有账本要一致。
  2. **没有中心账本**：事实由两方互签，各存一份。任一方单独改写历史，
     对方手里的副本就是反证。
  3. **没有全局目录**：发现靠邻居。你能看到多大的网，取决于你连了多少人 ——
     这不是缺陷，是"没有人能替你决定该看见谁"。

**一个节点 = 一个进程 = 一个库。** a2n-store 在 import 时读 A2N_DB 并缓存连接，
所以库路径必须在 import 之前定好（用 a2n_node.home.use_home，它零依赖）。
这不是妥协，正是"自持"的意思：你的库在你的机器上，别人碰不到。若库对不上，
本模块选择**当场报错**而不是照跑 —— 悄悄写进别人的库是最难查的事故。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from a2n_kernel.hashing import now_iso
from a2n_notary import notary
from a2n_p2p import Identity, P2PNode, fingerprint_of
from a2n_store import init_db
from a2n_store.db import DB_PATH as STORE_DB_PATH

from . import card as cardmod
from . import peer as peermod
from . import receipt as rcpt
from .home import (card_path, db_path_of, identity_path, read_json, same_path,
                   write_json)

# 无托管模式下"结算方式"只有两种，且都不是钱：
FREE = "free"                # 能力免费，天然无需结算
BILATERAL = "bilateral"      # 双边记账：两边各自记，定期互对（a2n-deal 的语汇）


class CallOutcome:
    """一次调用的结论。字段与平台模式同形，便于日后接回平台。"""

    def __init__(self, *, ok: bool, task_id: str, state: str, peer_did: str = "",
                 result: Any = None, usage: dict | None = None,
                 receipt: dict | None = None, error: str = "",
                 settle_mode: str = FREE, chain_ok: bool = True) -> None:
        self.ok = ok
        self.task_id = task_id
        self.state = state
        self.peer_did = peer_did
        self.result = result
        self.usage = usage or {}
        self.receipt = receipt
        self.error = error
        self.settle_mode = settle_mode
        self.chain_ok = chain_ok

    def to_dict(self) -> dict:
        return {"ok": self.ok, "task_id": self.task_id, "state": self.state,
                "peer_did": self.peer_did, "result": self.result,
                "usage": self.usage, "receipt": self.receipt, "error": self.error,
                "settle_mode": self.settle_mode, "chain_ok": self.chain_ok}

    def __repr__(self) -> str:
        head = "OK" if self.ok else "FAILED"
        return f"<CallOutcome {head} task={self.task_id} peer={self.peer_did[:24]} …>"


class SovereignNode:
    """一个自持节点：自己的钥匙、自己的库、自己的入口、自己的准入策略。"""

    def __init__(self, *, name: str, skills: list[Any], home: str | Path,
                 host: str = "127.0.0.1", port: int = 0,
                 advertise_host: str | None = None, p2p_port: int = 9610,
                 bootstrap: list[tuple[str, int]] | None = None,
                 beacon: bool = False,
                 executor: dict[str, Callable[[dict], Any]] | None = None,
                 open_skills: list[str] | None = None,
                 allow_dids: set[str] | None = None,
                 verbose: bool = False) -> None:
        self.name = name
        self.verbose = verbose
        self.home = Path(home).expanduser().resolve()
        self.home.mkdir(parents=True, exist_ok=True)

        want_db = db_path_of(self.home)
        if not same_path(STORE_DB_PATH, want_db):
            raise RuntimeError(
                f"库对不上：这个进程的 a2n-store 指向 {STORE_DB_PATH}，"
                f"而节点 {name} 的家是 {want_db}。\n"
                f"请在本进程 import 任何 a2n 包**之前**调用 "
                f"a2n_node.home.use_home({str(self.home)!r})。"
            )

        # 自己的库自己建表：平台模式下这一步是平台的启动动作，
        # 无托管模式下没有别人会替你做 —— 节点是它自己这个库的唯一责任方。
        init_db()
        self.identity = self._load_identity()
        self.executor = dict(executor or {})
        self.open_skills = set(open_skills) if open_skills is not None else None
        self.allow_dids = set(allow_dids or ())
        self.guard = peermod.ReplayGuard()
        self._acks: dict[str, dict] = {}

        # ① 先定入口（端口可能是 0，要绑定之后才知道真号），
        # ② 再据真实端口造卡，③ 最后把"卡哈希 + 业务入口"播给邻居。
        self.http = peermod.serve(self, host, port)
        self.port = self.http.server_address[1]
        adv_host = advertise_host or host
        endpoint = f"http://{adv_host}:{self.port}"
        self.card = cardmod.build_card(
            self.identity, name=name, skills=skills, host=adv_host, port=self.port,
            p2p_port=p2p_port)
        self.endpoint = self.card["url"]
        write_json(card_path(self.home), self.card)
        ok, why = cardmod.verify_card(self.card)
        if not ok:                       # 自证失败说明本进程有问题，别带着病上线
            raise RuntimeError(f"自造的卡验不过（{why}）")

        self.p2p = P2PNode(self.identity, port=p2p_port, bootstrap=bootstrap or [],
                           beacon=beacon, host="0.0.0.0", advertise_host=adv_host,
                           advert=self.advert())
        self.skills = cardmod.card_skills(self.card)

    # ---------------- 生命周期 ----------------

    def advert(self) -> dict:
        """给邻居的寻址通告：业务入口 + 卡哈希。

        卡哈希一起播出去，是为了让取卡这一步**可核对**：取回来的卡必须与我
        在发现阶段听到的哈希一致。否则发现层就成了换卡的旁路
        （gossip 里说 A，HTTP 上给你 B）。
        """
        return {"http": self.endpoint, "card_hash": cardmod.card_hash(self.card)}

    def start(self) -> "SovereignNode":
        self.p2p.start()
        self.p2p.announce(self.skills, self.advert())
        if self.verbose:
            print(f"[{self.name}] 上线 {self.identity.did}", flush=True)
            print(f"[{self.name}] 能力 {self.skills} · 入口 {self.endpoint}", flush=True)
        return self

    def stop(self) -> None:
        self.p2p.stop()
        self.http.shutdown()
        self.http.server_close()

    def stats(self) -> dict:
        return {"name": self.name, "did": self.identity.did, "skills": self.skills,
                "endpoint": self.endpoint, "chain": notary.verify()[1],
                "p2p": dict(self.p2p.stats), "replay_blocked": self.guard.size()}

    # ---------------- 身份与卡片 ----------------

    def _load_identity(self) -> Identity:
        """钥匙存在家里的文件里：重启还是同一个人，不需要向谁"重新注册"。"""
        p = identity_path(self.home)
        if p.exists():
            return Identity.load(p)
        ident = Identity.generate()
        ident.save(p)
        return ident

    @property
    def did(self) -> str:
        return self.identity.did

    def save_card(self) -> None:
        write_json(card_path(self.home), self.card)

    def set_skills(self, skills: list[Any]) -> None:
        """改能力＝换一张卡（要重新签名），然后重新播出去。"""
        self.card = cardmod.build_card(
            self.identity, name=self.name, skills=skills, host=self.card["url"].split("//")[1].rsplit(":", 1)[0],
            port=self.port, p2p_port=self.p2p.port)
        self.skills = cardmod.card_skills(self.card)
        self.save_card()
        self.p2p.advert = self.advert()
        self.p2p.announce(self.skills, self.advert())

    # ---------------- 发现 ----------------

    def discover(self, skill: str, timeout: float = 2.0) -> list[dict]:
        """按需发现：问邻居"谁会这个"。返回一组候选通告（含 did 与业务入口）。"""
        return self.p2p.query(skill, timeout=timeout)

    def find(self, skill: str, *, timeout: float = 2.0) -> tuple[dict | None, str]:
        """发现 → 取卡 → 验卡 → 核对哈希。返回 (card, 说明)。

        四步一个都不能省：
          发现只给出"谁自称会"（自报，不可信）；
          取卡拿回的是**它签名的**卡；
          验卡确认身份自洽且没被改；
          核对哈希确认取到的卡与它在 gossip 里播的是同一张。
        """
        offers = self.discover(skill, timeout=timeout)
        if not offers:
            return None, f"邻居里没有人提供 {skill}"
        tried = []
        for o in offers:
            did, advert = o.get("did"), o.get("advert") or {}
            descriptors = advert.get("cards") if isinstance(advert, dict) else None
            if not isinstance(descriptors, list):
                descriptors = [{"endpoint": advert.get("http"),
                                "card_hash": advert.get("card_hash"),
                                "skills": o.get("skills") or []}]
            for descriptor in descriptors:
                if not isinstance(descriptor, dict):
                    continue
                offered = descriptor.get("skills") or []
                if offered and skill not in offered:
                    continue
                endpoint, told = descriptor.get("endpoint"), descriptor.get("card_hash")
                source_host = o.get("_source_host")
                if not did or not endpoint or not told or not source_host:
                    tried.append(f"{str(did)[:20]}：通告缺少可核对的入口或卡哈希")
                    continue
                # Reuse the persistent runtime's hardened no-redirect, body-cap,
                # source-pinned card resolver instead of maintaining a weaker
                # second fetch path for sovereign nodes.
                from .p2p_service import P2PDiscoveryService
                got = P2PDiscoveryService._fetch_card(
                    str(endpoint), min(float(timeout), 3.0), str(source_host))
                if not got:
                    tried.append(f"{did[:20]}：取卡失败（{endpoint}）")
                    continue
                ok, why = cardmod.verify_card(got, require_endpoint=True)
                if not ok:
                    tried.append(f"{did[:20]}：卡验不过（{why}）")
                    continue
                if cardmod.card_did(got) != did:
                    tried.append(f"{did[:20]}：拿到的卡属于 {cardmod.card_did(got)}")
                    continue
                if told != cardmod.card_hash(got):
                    tried.append(f"{did[:20]}：卡哈希与发现阶段播的不一致")
                    continue
                if str(got.get("url") or "").rstrip("/") != str(endpoint).rstrip("/"):
                    tried.append(f"{did[:20]}：卡片入口与发现通告不一致")
                    continue
                if not cardmod.offers(got, skill):
                    tried.append(f"{did[:20]}：卡上并没有这个能力")
                    continue
                return got, "ok"
        return None, "；".join(tried) or "没有可用候选"

    # ---------------- 调用（作为调用方） ----------------

    def call(self, skill: str, payload: Any = None, *, card: dict | None = None,
             timeout: float = 30.0, settle_mode: str = FREE) -> CallOutcome:
        """直接调用对方的节点。**没有中间人**：请求打到对方自己的入口上。

        验完对方的收据后，本节点立刻签一份回执送回去 —— 这一步不是礼貌，
        是互证闭环的另一半：没有它，对方就没有"你收到了"的证据。
        """
        if card is None:
            card, why = self.find(skill, timeout=min(timeout, 3.0))
            if card is None:
                return CallOutcome(ok=False, task_id="", state="UNREACHABLE",
                                   error=why, settle_mode=settle_mode)
        peer_did = cardmod.card_did(card) or ""
        endpoint = cardmod.card_endpoint(card) or ""
        req = peermod.sign_request(self.identity, provider_did=peer_did, skill=skill,
                                  payload=payload)
        self._note("task.created", {
            "task_id": req["task_id"], "requester_id": self.did, "node_id": peer_did,
            "skill_id": skill, "source": "sovereign",
            "payload_hash": req["payload_hash"], "settle_mode": settle_mode})

        raw = peermod.call_direct(endpoint, req, timeout=timeout)
        if raw.get("_http_status"):
            self._fail(req["task_id"], f"对端返回 HTTP {raw['_http_status']}")
            return CallOutcome(ok=False, task_id=req["task_id"], state="FAILED",
                               peer_did=peer_did, error=raw.get("error", ""),
                               settle_mode=settle_mode)

        ok, why = peermod.verify_response(raw, req=req)
        if not ok:
            self._fail(req["task_id"], f"应答验不过：{why}")
            return CallOutcome(ok=False, task_id=req["task_id"], state="INVALID",
                               peer_did=peer_did, error=why,
                               settle_mode=settle_mode)
        if raw.get("state") != "accepted":
            self._fail(req["task_id"], f"对方未交付：{raw.get('error') or raw.get('state')}")
            return CallOutcome(ok=False, task_id=req["task_id"], state="FAILED",
                               peer_did=peer_did, error=raw.get("error", ""),
                               settle_mode=settle_mode)

        # 收据三重核对：谁签的 / 是哪一单 / 结果对得上吗
        receipt = raw.get("receipt") or {}
        ok, why = rcpt.verify(receipt)
        if not ok:
            self._fail(req["task_id"], f"收据验不过：{why}")
            return CallOutcome(ok=False, task_id=req["task_id"], state="INVALID",
                               peer_did=peer_did, error=why,
                               settle_mode=settle_mode)
        if receipt.get("by") != peer_did:
            self._fail(req["task_id"], "收据不是接单方签的")
            return CallOutcome(ok=False, task_id=req["task_id"], state="INVALID",
                               peer_did=peer_did, error="收据签名者与接单方不符",
                               settle_mode=settle_mode)
        if receipt.get("task_id") != req["task_id"] or \
                receipt.get("input_hash") != req["payload_hash"]:
            self._fail(req["task_id"], "收据对不上这一单（改过单号或输入）")
            return CallOutcome(ok=False, task_id=req["task_id"], state="INVALID",
                               peer_did=peer_did, error="收据与请求不符",
                               settle_mode=settle_mode)
        if receipt.get("output_hash") != rcpt.hash_payload(raw.get("result")):
            self._fail(req["task_id"], "收据里的输出哈希与实际结果不符")
            return CallOutcome(ok=False, task_id=req["task_id"], state="INVALID",
                               peer_did=peer_did, error="结果与收据不符",
                               settle_mode=settle_mode)

        # 本地落一枚章：把**对方签的收据原文**存进自己的链里
        self._note("acceptance.passed", {
            "task_id": req["task_id"], "node_id": peer_did, "skill_id": skill,
            "state": "accepted", "receipt_hash": rcpt.fingerprint(receipt),
            "output_hash": receipt.get("output_hash"),
            "receipt": receipt, "settle_mode": settle_mode})
        # 回执送回去，对方也握有一份带我方签名的证据
        delivered = peermod.send_ack(endpoint, rcpt.ack(self.identity, receipt),
                                     timeout=min(timeout, 10.0))
        chain_ok = notary.verify()[0]
        return CallOutcome(ok=True, task_id=req["task_id"], state="ACCEPTED",
                           peer_did=peer_did, result=raw.get("result"),
                           usage=raw.get("usage") or {}, receipt=receipt,
                           settle_mode=settle_mode, chain_ok=chain_ok
                           and delivered)

    # ---------------- 服务（作为供给方） ----------------

    def handle_call(self, req: dict) -> tuple[int, dict]:
        """收到一次调用。返回 (HTTP 码, 应答体)。

        顺序是刻意的：**先验身份，再谈业务**。签名、时间窗、载荷完整性都过了，
        才开始问"我提不提供这个能力""准不准你调"。反过来（先看业务再验签）
        等于把准入策略建立在不可信的自报字段上。
        """
        ok, why = peermod.verify_request(
            req, guard=self.guard, expected_provider=self.did)
        if not ok:
            return 401, {"error": why, "stage": "identity"}
        caller = req["caller_did"]
        skill = req["skill"]
        if skill not in self.skills:
            return 403, {"error": f"本节点不提供 {skill}", "stage": "capability"}
        allow, reason = self._admit(caller, skill)
        if not allow:
            return 403, {"error": reason, "stage": "policy"}
        body = rcpt.make_body(task_id=req["task_id"], caller_did=caller,
                              provider_did=self.did, skill=skill,
                              input_hash=req["payload_hash"], output_hash="",
                              ts=now_iso())
        self._note("task.created", {
            "task_id": req["task_id"], "requester_id": caller, "node_id": self.did,
            "skill_id": skill, "source": "sovereign",
            "payload_hash": req["payload_hash"], "settle_mode": FREE})

        fn = self.executor.get(skill)
        if fn is None:
            self._fail(req["task_id"], "节点没有装配这个能力的执行体")
            return 200, peermod.sign_response(self.identity, req=req, state="failed",
                                              error="节点没有装配这个能力的执行体",
                                              receipt=None)
        try:
            result = fn(req.get("payload"))
        except Exception as e:  # noqa: BLE001 - 执行体是别人写的，异常必须如实回报
            self._fail(req["task_id"], f"执行失败：{type(e).__name__}: {e}")
            return 200, peermod.sign_response(self.identity, req=req, state="failed",
                                              error=f"{type(e).__name__}: {e}",
                                              receipt=None)

        usage = {"call_count": 1}
        body["output_hash"] = rcpt.hash_payload(result)
        body["usage"] = usage
        body["state"] = "accepted"
        receipt = rcpt.sign(self.identity, body)
        # 两枚章：交付 + 自验收。无托管模式里"验收"没有第三方策略，
        # 它记的就是"我按这个输入产出了这个输出"——不假装有独立质检。
        self._note("task.submitted", {
            "task_id": req["task_id"], "node_id": self.did, "skill_id": skill,
            "result_hash": body["output_hash"], "usage": usage})
        self._note("acceptance.passed", {
            "task_id": req["task_id"], "node_id": self.did, "skill_id": skill,
            "state": "accepted", "receipt_hash": rcpt.fingerprint(receipt),
            "output_hash": body["output_hash"], "receipt": receipt})
        return 200, peermod.sign_response(self.identity, req=req, state="accepted",
                                          result=result, usage=usage, receipt=receipt)

    def handle_ack(self, ack: dict) -> tuple[int, dict]:
        """收到对方的回执：核对我自己发出的那一份收据，落一枚章。"""
        task_id = ack.get("task_id") or ""
        mine = self._receipt_for(task_id)
        if mine is None:
            return 404, {"error": f"本地链里没有 {task_id} 的收据，无从核对"}
        ok, why = rcpt.verify_ack(ack, mine)
        if not ok:
            return 401, {"error": why}
        self._acks[task_id] = ack
        self._note("delivery.acknowledged", {
            "task_id": task_id, "ack_by": ack.get("by"), "of": ack.get("of")})
        return 200, {"ok": True, "task_id": task_id}

    def _admit(self, caller_did: str, skill: str) -> tuple[bool, str]:
        """准入策略：**这是无托管模式下唯一的信任决定，所以它必须在本地、可读**。

        默认（open_skills=None）：能力对任何人开放 —— 一个只接免费能力的节点
        不需要认识对方。受限能力（列进 open_skills 之外的）只接受白名单里的 did。
        去掉中心之后，"信不信你"本就该由节点自己说，而不是去查一个并不存在的
        全局信誉。
        """
        if self.open_skills is None or skill in self.open_skills:
            return True, "ok"
        if caller_did in self.allow_dids:
            return True, "ok"
        return False, f"能力 {skill} 未对外开放，且 {caller_did[:24]} 不在本节点白名单里"

    # ---------------- 本地链（刻章） ----------------

    def _note(self, event_type: str, payload: dict) -> None:
        notary.record(event_type, payload)

    def _fail(self, task_id: str, reason: str) -> None:
        self._note("task.failed", {"task_id": task_id, "reason": reason})

    def chain_view(self, limit: int = 20) -> dict:
        items = notary.recent(limit)
        ok, msg = notary.verify()
        return {"did": self.did, "chain_ok": ok, "chain": msg, "items": items}

    def verify_chain(self) -> tuple[bool, str]:
        return notary.verify()

    def receipts_of(self, task_id: str) -> list[dict]:
        return notary.for_task(task_id)

    def _receipt_for(self, task_id: str) -> dict | None:
        """从**本地链**里取回我发出的那份收据 —— 不另建一张表。

        事实只存一处（链），重启后照样能核对回执；多存一份就是第二份事实。
        """
        for row in notary.for_task(task_id):
            try:
                payload = json.loads(row["payload"])
            except (TypeError, ValueError):
                continue
            if payload.get("receipt"):
                return payload["receipt"]
        return None

    # ---------------- 视图 ----------------

    def card_json(self) -> dict:
        return self.card

    def peers(self) -> list[dict]:
        return [{"did": p.did, "addr": list(p.addr), "skills": p.skills,
                 "advert": p.advert, "endorsed": bool(p.pub_raw)}
                for p in self.p2p.table.alive()]

    def fingerprint(self) -> str:
        return fingerprint_of(self.identity.pub_raw)


def wait_for_http(url: str, timeout: float = 10.0) -> bool:
    """等一个对端入口起来（跨进程演示时用：进程起来了不等于端口在听）。"""
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.time() < deadline:
        try:
            with opener.open(url.rstrip("/") + "/a2n/health", timeout=1.5) as f:
                if f.status == 200:
                    return True
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
            time.sleep(0.15)
    return False
