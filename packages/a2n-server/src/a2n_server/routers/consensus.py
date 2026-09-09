"""共识接口：封批次、收见证、出锚、定案、SPV 验证。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from a2n_consensus import consensus
from .. import wiring

router = APIRouter(prefix="/v1/consensus", tags=["consensus"])


class SealIn(BaseModel):
    threshold: int | None = None


class WitnessIn(BaseModel):
    signer_did: str | None = None
    all: bool = False


@router.get("/epochs")
def list_epochs(limit: int = 50):
    return consensus.list_epochs(limit=limit)


@router.get("/epochs/{epoch_id}")
def epoch_state(epoch_id: str):
    st = consensus.state_of(epoch_id)
    if not st:
        raise HTTPException(404, "epoch 不存在")
    return st


@router.post("/epochs/seal")
def seal(body: SealIn):
    ep = wiring.maybe_seal_epoch(body.threshold)
    if ep is None:
        return {"sealed": False, "why": "账目增量未达到阈值"}
    return {"sealed": True, "epoch": ep.to_dict()}


@router.post("/epochs/{epoch_id}/witness")
def witness(epoch_id: str, body: WitnessIn):
    """收见证签名。演示模式：signer_did 为空 + all=true 时由本地见证人代签。

    生产模式里签名从 p2p 的 EPOCH/ANCHOR 消息到达，这里只做收集与存储。
    """
    try:
        out = []
        dids = wiring.witness_dids() if body.all else [body.signer_did]
        for did in dids:
            if not did:
                raise HTTPException(400, "需要 signer_did 或 all=true")
            sig = wiring.witness_sign_epoch(epoch_id, did)
            consensus.witness_sign(epoch_id, sig["signer"], sig["sig"])
            out.append(sig)
        return {"collected": len(out), "sigs": out}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/epochs/{epoch_id}/anchor")
def anchor(epoch_id: str):
    """演示模式：本进程的持牌方 Mock 出具资金锚。生产由持牌方 HSM 签发。"""
    try:
        sig = wiring.anchor_sign_epoch(epoch_id)
        consensus.add_anchor(epoch_id, sig)   # 收下锚，定案时才验
        return sig
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/epochs/{epoch_id}/finalize")
def finalize(epoch_id: str):
    try:
        return consensus.finalize(epoch_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


class ProofIn(BaseModel):
    epoch_id: str
    entry_id: str


@router.post("/proof")
def proof(body: ProofIn):
    """SPV：证明"这条账目在这批已锚定的账里"。

    轻节点只需要证明 + 根，不需要全量账本 —— 这是家宽电脑
    能独立核账、而不必同步全网数据的原因。
    """
    try:
        p = consensus.proof(body.epoch_id, _index_of(body.epoch_id, body.entry_id))
    except (ValueError, IndexError) as e:
        raise HTTPException(400, str(e))
    return p


def _index_of(epoch_id: str, entry_id: str) -> int:
    ep = consensus._get(epoch_id)
    if not ep:
        raise ValueError("epoch 不存在")
    entries = consensus._require_batch(ep.from_rowid, ep.to_rowid)
    for i, e in enumerate(entries):
        if e.get("id") == entry_id:
            return i
    raise ValueError(f"条目 {entry_id} 不在 epoch {epoch_id} 的批次里")


@router.get("/verify")
def verify_chain():
    ok, msg = consensus.verify_chain()
    return {"ok": ok, "message": msg}
