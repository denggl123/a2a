"""epoch：把一个区间的账目压成一个根，并集齐三把钥匙。

流程（刻意做成三段，因为三段的责任人不同）：

    open()     记录起点        —— 谁都能开，只是个游标
    seal()     算出 Merkle 根  —— 需要账本（节点）
    finalize() 集齐三钥匙      —— 需要 ≥2/3 见证（网络）+ 资金锚（持牌方）

三段之间允许停摆：见证人不来签名，epoch 就永远停在 SEALED。
**停摆优于错误地通过** —— 这是共识层唯一正确的失败取向。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Any, Callable, Sequence

from a2n_store import conn
from a2n_kernel.events import publish
from a2n_kernel.hashing import GENESIS, chain_hash, new_id, now_iso, sha256
from a2n_kernel.merkle import EMPTY_ROOT, merkle_proof, merkle_root, verify_proof

from .anchor import Anchor, three_keys_ok
from .witness import WitnessSet

BatchProvider = Callable[[int, int], list[dict]]     # (from_rowid, to_rowid) -> entries
LeafFn = Callable[[dict], str]
Verifier = Callable[[str, str, str], bool]
EscrowFn = Callable[[], int]

STATE_OPEN, STATE_SEALED, STATE_ANCHORED = "OPEN", "SEALED", "ANCHORED"


@dataclass
class Epoch:
    epoch_id: str
    seq: int
    from_rowid: int
    to_rowid: int
    entry_count: int
    merkle_root: str
    prev_hash: str
    epoch_hash: str
    escrow_balance_fen: int
    state: str
    created_at: str
    sealed_at: str | None
    anchored_at: str | None

    def to_dict(self) -> dict:
        return asdict(self)

    def sign_msg(self) -> str:
        """签名域：只签"这批账是什么"，不签状态与时间戳。

        状态是本地进度，不同节点进度不同；把状态放进签名域会让
        同一个 epoch 在不同节点上签名失效，共识直接卡死。
        """
        return sha256(json.dumps({
            "epoch_id": self.epoch_id, "seq": self.seq,
            "from": self.from_rowid, "to": self.to_rowid,
            "count": self.entry_count, "root": self.merkle_root, "prev": self.prev_hash,
        }, sort_keys=True, separators=(",", ":")))


class EpochService:
    def __init__(self) -> None:
        self._batch: BatchProvider | None = None
        self._leaf: LeafFn = lambda e: e["hash"]
        self._verifier: Verifier | None = None
        self._escrow: EscrowFn | None = None
        self.witnesses = WitnessSet()
        self.anchor: Anchor | None = None

    # ---------- 装配（全部注入，本包不认识账本与密码学库）----------
    def set_batch_provider(self, fn: BatchProvider) -> None:
        self._batch = fn

    def set_leaf(self, fn: LeafFn) -> None:
        self._leaf = fn

    def set_verifier(self, fn: Verifier) -> None:
        self._verifier = fn

    def set_escrow(self, fn: EscrowFn) -> None:
        self._escrow = fn

    def _require_batch(self, a: int, b: int) -> list[dict]:
        if self._batch is None:
            raise RuntimeError("未注入 batch_provider：共识层不知道账本长什么样，必须由装配层接线")
        return list(self._batch(a, b))

    # ---------- 生命周期 ----------
    def _next_seq(self) -> int:
        row = conn().execute("SELECT COALESCE(MAX(seq),0) s FROM epochs").fetchone()
        return int(row["s"]) + 1

    def _prev_hash(self) -> str:
        row = conn().execute("SELECT epoch_hash FROM epochs ORDER BY seq DESC LIMIT 1").fetchone()
        return row["epoch_hash"] if row else GENESIS

    def _head_rowid(self) -> int:
        """批次起点 = 上一个 epoch 的终点；没有上一个就从 0 开始。"""
        row = conn().execute("SELECT COALESCE(MAX(to_rowid),0) t FROM epochs").fetchone()
        return int(row["t"])

    def open(self, from_rowid: int | None = None) -> Epoch:
        start = self._head_rowid() if from_rowid is None else int(from_rowid)
        ep = Epoch(epoch_id=new_id("ep"), seq=self._next_seq(), from_rowid=start,
                   to_rowid=start, entry_count=0, merkle_root=EMPTY_ROOT,
                   prev_hash=self._prev_hash(), epoch_hash="", escrow_balance_fen=0,
                   state=STATE_OPEN, created_at=now_iso(), sealed_at=None, anchored_at=None)
        conn().execute(
            "INSERT INTO epochs (epoch_id, seq, from_rowid, to_rowid, entry_count, merkle_root,"
            " prev_hash, epoch_hash, escrow_balance_fen, state, created_at, sealed_at, anchored_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ep.epoch_id, ep.seq, ep.from_rowid, ep.to_rowid, ep.entry_count, ep.merkle_root,
             ep.prev_hash, ep.epoch_hash, ep.escrow_balance_fen, ep.state, ep.created_at, None, None),
        )
        conn().commit()
        publish("epoch.opened", {"epoch_id": ep.epoch_id, "from_rowid": ep.from_rowid})
        return ep

    def seal(self, epoch_id: str | None = None) -> Epoch:
        """拉取区间账目，算 Merkle 根。空批次也允许封（根 = EMPTY_ROOT）。"""
        ep = self._get(epoch_id) if epoch_id else self._latest()
        if ep is None:
            raise ValueError("没有可封口的 epoch")
        if ep.state != STATE_OPEN:
            raise ValueError(f"epoch {ep.epoch_id} 状态为 {ep.state}，不可封口")

        # 封口 = 取到此刻的账本高水位。open 只记起点，终点由封口时刻决定。
        entries = self._require_batch(ep.from_rowid, None)
        to_rowid = entries[-1]["rowid"] if entries else ep.from_rowid
        leaves = [self._leaf(e) for e in entries]
        root = merkle_root(leaves)
        ts = now_iso()
        ep.entry_count = len(entries)
        ep.to_rowid = int(to_rowid)
        ep.merkle_root = root
        ep.epoch_hash = chain_hash(ep.prev_hash, {
            "epoch_id": ep.epoch_id, "seq": ep.seq, "from": ep.from_rowid, "to": ep.to_rowid,
            "count": ep.entry_count, "root": root, "ts": ts,
        })
        ep.state = STATE_SEALED
        ep.sealed_at = ts
        ep.escrow_balance_fen = int(self._escrow() or 0) if self._escrow else 0
        conn().execute(
            "UPDATE epochs SET to_rowid=?, entry_count=?, merkle_root=?, epoch_hash=?, state=?,"
            " escrow_balance_fen=?, sealed_at=? WHERE epoch_id=?",
            (ep.to_rowid, ep.entry_count, ep.merkle_root, ep.epoch_hash, ep.state,
             ep.escrow_balance_fen, ts, ep.epoch_id),
        )
        conn().commit()
        publish("epoch.sealed", {"epoch_id": ep.epoch_id, "root": root,
                                 "count": ep.entry_count, "from": ep.from_rowid, "to": ep.to_rowid,
                                 "escrow_fen": ep.escrow_balance_fen})
        return ep

    def witness_sign(self, epoch_id: str, signer: str, sig: str) -> dict:
        """收下一枚见证签名。签名本身不校验真伪 —— 真伪在 finalize 时一次性验。

        为什么要存下来再验：见证人可能先于账本同步到位，
        存下证据（哪怕是坏签名）本身就是追责材料。
        """
        conn().execute(
            "INSERT INTO epoch_sigs (id, epoch_id, signer, kind, sig, attestation, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (new_id("sg"), epoch_id, signer, "witness", sig, None, now_iso()),
        )
        conn().commit()
        return {"epoch_id": epoch_id, "signer": signer, "stored": True}

    def add_anchor(self, epoch_id: str, sig: dict) -> dict:
        conn().execute(
            "INSERT INTO epoch_sigs (id, epoch_id, signer, kind, sig, attestation, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (new_id("sg"), epoch_id, sig.get("signer", "?"), "anchor", sig.get("sig", ""),
             json.dumps(sig.get("attestation") or {}, ensure_ascii=False), now_iso()),
        )
        conn().commit()
        return {"epoch_id": epoch_id, "anchored_by": sig.get("signer")}

    def finalize(self, epoch_id: str) -> dict:
        """集齐三把钥匙才通过。缺任何一把就停在 SEALED，绝不降级放行。"""
        ep = self._get(epoch_id)
        if ep is None:
            raise ValueError("epoch 不存在")
        if ep.state == STATE_ANCHORED:
            return {"epoch_id": epoch_id, "state": STATE_ANCHORED, "already": True}

        msg = ep.sign_msg()
        wsigs = self._sigs(epoch_id, "witness")
        asigs = self._sigs(epoch_id, "anchor")

        # 钥匙一：账本批次可独立重算。
        # 注意这里不是"重新收集 hash 字段"（那是同义反复——篡改 delta 时
        # hash 字段不会自己变），而是**重算每条账目的哈希链**：
        # delta 被改 → chain_hash 对不上；hash 字段被改 → 与 prev_hash 断链。
        entries = self._require_batch(ep.from_rowid, ep.to_rowid)
        recomputed, tamper = self._recompute_root(entries)
        entries_ok = (recomputed is not None and recomputed == ep.merkle_root)
        if not entries_ok:
            why = tamper or "账本重算的 Merkle 根与封口时不一致"
            publish("epoch.rejected", {"epoch_id": epoch_id, "why": why})
            return {"epoch_id": epoch_id, "state": ep.state, "passed": False,
                    "why": f"账本已被篡改：{why}"}

        # 钥匙二：≥2/3 见证权重
        if self._verifier is None:
            return {"epoch_id": epoch_id, "state": ep.state, "passed": False,
                    "why": "未注入验签器，无法验证见证签名"}
        wok, why_w = self.witnesses.verify(wsigs, msg, self._verifier)

        # 钥匙三：资金锚
        if not asigs or self.anchor is None:
            aok, why_a = False, "缺少资金锚"
        else:
            aok, why_a = self.anchor.verify(asigs[-1], epoch_id)

        passed = three_keys_ok(entries_ok, wok, aok)
        if not passed:
            publish("epoch.rejected", {"epoch_id": epoch_id, "why": f"{why_w}；{why_a}"})
            return {"epoch_id": epoch_id, "state": ep.state, "passed": False,
                    "why": why_w, "anchor": why_a}

        ts = now_iso()
        conn().execute("UPDATE epochs SET state=?, anchored_at=? WHERE epoch_id=?",
                       (STATE_ANCHORED, ts, epoch_id))
        conn().commit()
        payload = {"epoch_id": epoch_id, "seq": ep.seq, "root": ep.merkle_root,
                   "count": ep.entry_count, "escrow_fen": ep.escrow_balance_fen,
                   "witness_weight": self.witnesses.signed_weight(wsigs),
                   "witness_total": self.witnesses.total_weight()}
        publish("epoch.anchored", payload)
        return {"epoch_id": epoch_id, "state": STATE_ANCHORED, "passed": True, **payload}

    def _recompute_root(self, entries: list[dict]) -> tuple[str | None, str | None]:
        """从原始字段重算批次 Merkle 根；任何一条对不上都返回失败原因。

        批次起点的前驱哈希（entries[0].prev_hash）是上一个 epoch 锚定过的
        最后一条的 hash —— 链的前段已被上个 epoch 承诺，这里只验本批次内部。
        """
        prev_leaf: str | None = None
        leaves: list[str] = []
        for e in entries:
            expect = chain_hash(e["prev_hash"], {
                "id": e["id"], "account": e["account_id"], "delta": e["delta"],
                "ref_type": e["ref_type"], "ref_id": e["ref_id"], "ts": e["created_at"],
            })
            if expect != e["hash"]:
                return None, f"条目 {e['id']} 的哈希与字段不符"
            if prev_leaf is not None and e["prev_hash"] != prev_leaf:
                return None, f"条目 {e['id']} 与前条断链"
            leaves.append(e["hash"])
            prev_leaf = e["hash"]
        return merkle_root(leaves), None

    # ---------- SPV：轻节点不必下载全量 ----------
    def proof(self, epoch_id: str, index: int) -> dict:
        ep = self._get(epoch_id)
        if ep is None:
            raise ValueError("epoch 不存在")
        entries = self._require_batch(ep.from_rowid, ep.to_rowid)
        leaves = [self._leaf(e) for e in entries]
        if not 0 <= index < len(leaves):
            raise IndexError(f"下标越界：{index} / {len(leaves)}")
        return {
            "epoch_id": epoch_id, "root": ep.merkle_root, "index": index,
            "leaf": leaves[index], "entry_id": entries[index].get("id"),
            "proof": merkle_proof(leaves, index),
        }

    @staticmethod
    def verify_inclusion(root: str, leaf: str, index: int, proof: Sequence[dict]) -> bool:
        return verify_proof(leaf, index, proof, root)

    # ---------- 查询 ----------
    def _row_to_epoch(self, r) -> Epoch | None:
        if not r:
            return None
        d = dict(r)
        return Epoch(epoch_id=d["epoch_id"], seq=d["seq"], from_rowid=d["from_rowid"],
                     to_rowid=d["to_rowid"], entry_count=d["entry_count"],
                     merkle_root=d["merkle_root"], prev_hash=d["prev_hash"],
                     epoch_hash=d["epoch_hash"], escrow_balance_fen=d["escrow_balance_fen"],
                     state=d["state"], created_at=d["created_at"], sealed_at=d["sealed_at"],
                     anchored_at=d["anchored_at"])

    def _get(self, epoch_id: str) -> Epoch | None:
        r = conn().execute("SELECT * FROM epochs WHERE epoch_id=?", (epoch_id,)).fetchone()
        return self._row_to_epoch(r)

    def _latest(self) -> Epoch | None:
        r = conn().execute("SELECT * FROM epochs ORDER BY seq DESC LIMIT 1").fetchone()
        return self._row_to_epoch(r)

    def _sigs(self, epoch_id: str, kind: str) -> list[dict]:
        rows = conn().execute(
            "SELECT * FROM epoch_sigs WHERE epoch_id=? AND kind=? ORDER BY rowid ASC", (epoch_id, kind)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("attestation"):
                d["attestation"] = json.loads(d["attestation"])
            out.append(d)
        return out

    def state_of(self, epoch_id: str) -> dict:
        ep = self._get(epoch_id)
        if not ep:
            return {}
        wsigs = self._sigs(epoch_id, "witness")
        return {
            **ep.to_dict(),
            "witness_sigs": len(wsigs),
            "witness_weight": self.witnesses.signed_weight(wsigs),
            "witness_total": self.witnesses.total_weight(),
            "threshold": self.witnesses.threshold(),
            "anchor": len(self._sigs(epoch_id, "anchor")) > 0,
        }

    def list_epochs(self, limit: int = 50) -> list[dict]:
        rows = conn().execute("SELECT * FROM epochs ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def verify_chain(self) -> tuple[bool, str]:
        """epoch 链自身的完整性：prev_hash 串联 + 状态单调。"""
        rows = conn().execute("SELECT * FROM epochs ORDER BY seq ASC").fetchall()
        prev = GENESIS
        for r in rows:
            if r["prev_hash"] != prev:
                return False, f"epoch 链断裂于 seq={r['seq']}"
            expect = chain_hash(prev, {
                "epoch_id": r["epoch_id"], "seq": r["seq"], "from": r["from_rowid"],
                "to": r["to_rowid"], "count": r["entry_count"], "root": r["merkle_root"],
                "ts": r["sealed_at"],
            })
            if r["sealed_at"] and r["epoch_hash"] != expect:
                return False, f"epoch seq={r['seq']} 哈希不匹配"
            prev = r["epoch_hash"] or prev
        return True, f"完整，共 {len(rows)} 个 epoch"


consensus = EpochService()
