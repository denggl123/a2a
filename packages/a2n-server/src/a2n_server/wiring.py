"""装配层接线：所有模块之间"谁连谁"，只在这个文件里写一次。

为什么要有它：模块化的代价就是"接线"必须有地方落脚。
散在各模块里 = 隐式耦合（改一处不知道会牵动谁）；
集中在一处 = 显式耦合，接线图就是这份文件，review 一次看全。

这里的每一条线都有明确的接线理由：

  events → outbox            内核不许碰存储，落库由装配层注入
  ledger → consensus         共识要知道账本，但不 import 它（依赖注入）
  custodian → consensus      资金锚的读数来自持牌方（唯一能碰钱的包）
  acceptance.failed → disputes   机器判不了的自动转人工
  arbitration.resolved → settlement  裁定产出"该怎么退"，执行交给结算层
  epoch.anchored → p2p gossip    锚好的批次向网络广播（ANCHOR 消息）
  p2p offers → rosters       按需发现的结果落到"我的本地市场列表"

演示环境的妥协（都有注释，生产替换点明确）：
  见证人与资金锚的私钥在本进程内存里生成 —— 真实部署中见证人是远端节点
  （签名经 p2p 到达），资金锚在持牌方 HSM 里。
"""
from __future__ import annotations

from typing import Any

from a2n_kernel import events
from a2n_store import outbox

_wired = False
_signers: dict[str, Any] = {}       # did -> Signer（演示见证人 + 锚）
_anchor_signer: Any = None
_epoch_threshold = 100
_p2p_node: Any = None               # 可选：本进程的 P2P 节点
_p2p_attached = False


def wire(p2p_node: Any = None, witnesses: int = 3, epoch_threshold: int = 100) -> dict:
    """装配全部接线。幂等：重复调用不重复订阅。"""
    global _wired, _anchor_signer, _epoch_threshold
    _epoch_threshold = epoch_threshold

    # 1. 事件落库：内核只管分发，持久化交给 outbox
    events.set_sink(outbox.append)

    if _wired:
        _refresh_consensus_wiring()
        return status()

    # 2. 共识层注入（它不 import 账本与持牌方，全部由这里递进去）
    _refresh_consensus_wiring()

    # 2b. 一期：额度检查需要"已开未结金额"，那是交易层的数据。
    #     L2 不能反向依赖 L3，所以由装配层把函数递进去。
    from a2n_account import peers
    from a2n_deal import deals as deal_book

    peers.set_exposure(deal_book.exposure_fen)

    # 3. 机器判不了的事转人工：验收失败自动开争议单
    from a2n_acceptance.dispute import disputes as dispute_book
    from a2n_kernel.hashing import new_id

    def on_acceptance_failed(e: dict) -> None:
        try:
            task_id = e.get("task_id")
            if not task_id:
                return
            reasons = e.get("reasons") or []
            dispute_book.open(
                task_id=task_id, side="node", opened_by="system",
                reason="验收自动判定未通过，节点可申诉：" + "; ".join(map(str, reasons)),
                evidence={"reasons": reasons},
            )
        except Exception:            # noqa: BLE001 - 接线失败不拖垮业务主流程
            pass

    events.subscribe("acceptance.failed", on_acceptance_failed)

    # 4. 裁定 → 执行：仲裁只判断，退钱由结算层做
    from a2n_store import conn
    from a2n_settlement import settlement as settlement_svc

    def on_arbitration_resolved(e: dict) -> None:
        try:
            refund_points = int(e.get("refund_points") or 0)
            if refund_points <= 0:
                return
            task_id = e.get("task_id")
            row = conn().execute("SELECT requester_id FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                return
            settlement_svc.refund(task_id, row["requester_id"], refund_points,
                                  reason=f"arbitration:{e.get('dispute_id')}")
        except Exception:            # noqa: BLE001
            pass

    events.subscribe("arbitration.resolved", on_arbitration_resolved)

    # 5. 锚好的批次向网络广播（没有 p2p 节点时静默跳过 —— 共识照常工作）
    if p2p_node is not None:
        attach_p2p(p2p_node)

    _wired = True
    return status()


def attach_p2p(node: Any) -> None:
    """把 P2P 节点挂上事件线：epoch 锚定后向 gossip 网络广播 ANCHOR。

    单独拆出来：p2p 节点的生死由部署决定（脚本/服务可随时起停），
    而接线必须常驻 —— 节点重启后重新 attach 即可，事件订阅不重复。
    """
    global _p2p_node, _p2p_attached
    _p2p_node = node
    if _p2p_attached:
        return

    def on_epoch_anchored(e: dict) -> None:
        try:
            if _p2p_node is None:
                return
            _p2p_node.gossip("ANCHOR", {
                "epoch_id": e.get("epoch_id"), "seq": e.get("seq"),
                "root": e.get("root"), "count": e.get("count"),
                "escrow_fen": e.get("escrow_fen"),
                "witness_weight": e.get("witness_weight"),
                "witness_total": e.get("witness_total"),
            })
        except Exception:            # noqa: BLE001
            pass

    events.subscribe("epoch.anchored", on_epoch_anchored)
    _p2p_attached = True


def _refresh_consensus_wiring() -> None:
    """共识层的注入点。每次调用都刷新（见证名单可能变化），订阅只做一次。"""
    global _signers, _anchor_signer
    from a2n_consensus import consensus
    from a2n_consensus.anchor import Anchor
    from a2n_consensus.witness import Witness
    from a2n_ledger import Ledger
    from a2n_custodian import get_custodian
    from a2n_p2p import Signer, verifier_fn

    ledger = Ledger()

    def batch_provider(from_rowid: int, to_rowid: int | None) -> list[dict]:
        return ledger.batch_range(from_rowid, to_rowid)

    consensus.set_batch_provider(batch_provider)
    consensus.set_leaf(Ledger.leaf)
    consensus.set_verifier(verifier_fn())
    consensus.set_escrow(lambda: get_custodian().balance_fen())

    # 演示见证人与资金锚：私钥只在本进程内存。生产替换点：
    #   见证人 = 远端节点公钥（经 p2p HELLO/注册表同步），签名随消息到达；
    #   资金锚 = 持牌方 HSM 签名。
    if not _signers:
        for _ in range(max(1, _WITNESS_COUNT)):
            s = Signer.generate()
            _signers[s.did] = s
        _anchor_signer = Signer.generate()
    for did, s in _signers.items():
        consensus.witnesses.add(Witness(did=did, pub=s.pub))
    consensus.anchor = Anchor(did=_anchor_signer.did, pub=_anchor_signer.pub,
                              signer=_anchor_signer.signer_fn(), verifier=verifier_fn())


_WITNESS_COUNT = 3


def witness_sign_epoch(epoch_id: str, signer_did: str) -> dict:
    """演示模式：让本地见证人为 epoch 签名。

    生产模式中这一步不存在 —— 签名从 p2p 到达，节点只做收集与验证。
    """
    s = _signers.get(signer_did)
    if not s:
        raise ValueError(f"未知见证人：{signer_did}")
    ep = consensus_epoch(epoch_id)
    return {"signer": signer_did, "sig": s.sign(ep.sign_msg()),
            "epoch_id": epoch_id}


def anchor_sign_epoch(epoch_id: str) -> dict:
    """演示模式：持牌方给 epoch 出资金锚。"""
    from a2n_consensus import consensus
    from a2n_custodian import get_custodian

    ep = consensus_epoch(epoch_id)
    att = consensus.anchor.attest(epoch_id, get_custodian().balance_fen(),
                                  custodian_ref=f"epoch:{epoch_id}")
    return consensus.anchor.sign(att)


def consensus_epoch(epoch_id: str):
    from a2n_consensus import consensus
    ep = consensus._get(epoch_id)
    if not ep:
        raise ValueError("epoch 不存在")
    return ep


def witness_dids() -> list[str]:
    return list(_signers.keys())


def maybe_seal_epoch(threshold: int | None = None) -> dict | None:
    """账目涨了 enough 条就封一个批次。返回封好的 epoch，没到阈值返回 None。

    这是"批次化"的节拍器：不是每条账都开共识（太贵），
    而是攒一批压一个根 —— 与区块链的出块间隔是同一个思路。
    """
    from a2n_consensus import consensus
    from a2n_ledger import Ledger

    th = threshold or _epoch_threshold
    ledger = Ledger()
    head = ledger.head_rowid()
    last = consensus.list_epochs(limit=1)
    last_to = last[0]["to_rowid"] if last else 0
    if head - last_to < th:
        return None
    ep = consensus.open()
    return consensus.seal(ep.epoch_id)


def p2p_discover_to_roster(p2p_node: Any, principal_id: str, skill: str,
                           timeout: float = 2.0) -> list[dict]:
    """P2P 按需发现 → 落入"我的本地市场列表"。

    这就是"发现后出现在我本地电脑的市场列表"的那条线：
    发现是一次网络行为，结果沉淀为本机的一张表，之后离线也能看。
    没人同步全量目录 —— 你看到的市场，是你自己问出来的那一份。
    """
    from a2n_store import conn
    from a2n_kernel.hashing import now_iso

    offers = p2p_node.query(skill, timeout=timeout)
    c = conn()
    row = c.execute("SELECT roster FROM rosters WHERE principal_id=?", (principal_id,)).fetchone()
    import json as _json

    roster: dict[str, dict] = {}
    if row:
        try:
            roster = {x["did"]: x for x in _json.loads(row["roster"])}
        except (ValueError, TypeError, KeyError):
            roster = {}
    for o in offers:
        did = o.get("did")
        if not did:
            continue
        roster[did] = {"did": did, "skills": o.get("skills") or [skill],
                       "skill": skill, "found_at": now_iso(), "via": "p2p"}
    payload = list(roster.values())
    c.execute(
        "INSERT INTO rosters (principal_id, roster, updated_at) VALUES (?,?,?)"
        " ON CONFLICT(principal_id) DO UPDATE SET roster=excluded.roster, updated_at=excluded.updated_at",
        (principal_id, _json.dumps(payload, ensure_ascii=False), now_iso()),
    )
    c.commit()
    return payload


def status() -> dict:
    return {
        "wired": _wired,
        "event_sink": "outbox",
        "witnesses": len(_signers),
        "anchor_ready": _anchor_signer is not None,
        "epoch_threshold": _epoch_threshold,
    }
