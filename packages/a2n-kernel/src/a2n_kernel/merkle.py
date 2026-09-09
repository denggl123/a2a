"""Merkle 树：把一批事实压成一个根，并能为其中任一条出具 SPV 证明。

为什么放在内核：
  它是纯算法，不认识任务、积分、账本。放这里任何层都能用，且不产生依赖方向问题。

为什么需要它：
  没有中心服务器之后，"我这批账和你那批账一样"这句话不能靠信任，只能靠
  一个双方都能独立算出来的根。而**验证单条账目是否被包含**只需要 O(log n)
  的证明，不必下载全量 —— 这是轻节点（家宽电脑）能参与共识的前提。

与哈希链的区别：
  哈希链证明"顺序未被篡改"（纵向），Merkle 证明"集合未被篡改"（横向）。
  两者叠加，单点篡改既改不动顺序也改不动集合。
"""
from __future__ import annotations

from typing import Iterable, Sequence

from .hashing import sha256

# 空集合的根。必须是一个公开的常量，否则"空批次"可以被随意构造。
EMPTY_ROOT = sha256("a2n:merkle:empty")


def _pair(left: str, right: str) -> str:
    return sha256(left + right)


def merkle_root(leaves: Sequence[str] | Iterable[str]) -> str:
    """计算 Merkle 根。奇数个叶子时最后一个自我配对（比特币式）。

    自我配对而非补齐空串：补齐会让不同大小的集合可能算出同一个根，
    存在第二原像风险；自我配对则保证每个根都唯一对应一个叶子序列。
    """
    level = [str(x) for x in leaves]
    if not level:
        return EMPTY_ROOT
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [_pair(level[i], level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def merkle_proof(leaves: Sequence[str], index: int) -> list[dict[str, str]]:
    """为 index 处的叶子生成证明路径。

    返回 [{"hash":..., "side":"left"|"right"}, ...]，从叶子往上直到根。
    side 表示**兄弟节点**位于哪一侧 —— 验证时按它决定拼接顺序。
    """
    level = [str(x) for x in leaves]
    if not level:
        return []
    if not 0 <= index < len(level):
        raise IndexError(f"叶子下标越界：{index} / {len(level)}")

    proof: list[dict[str, str]] = []
    i = index
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        if i % 2 == 0:
            sibling, side = (level[i + 1] if i + 1 < len(level) else level[i]), "right"
        else:
            sibling, side = level[i - 1], "left"
        proof.append({"hash": sibling, "side": side})
        level = [_pair(level[j], level[j + 1]) for j in range(0, len(level), 2)]
        i //= 2
    return proof


def verify_proof(leaf: str, index: int, proof: Sequence[dict[str, str]], root: str) -> bool:
    """验证"leaf 在 root 所代表的集合里"。

    诚实的边界：标准 Merkle 证明能证明"这个叶子属于这个集合"，
    但对内相邻的兄弟叶子（同一对里左右互换）用同一条证明也能通过 ——
    证明路径里没有编码对内位置。A2N 里叶子是账目自身的 hash
    （含 prev_hash），位置其实已由哈希链承诺，这里不需要重复编码。
    """
    h = str(leaf)
    i = int(index)
    for step in proof:
        s = step["hash"]
        h = _pair(h, s) if step["side"] == "right" else _pair(s, h)
        i //= 2
    return h == root


def proof_root(leaf: str, index: int, proof: Sequence[dict[str, str]]) -> str:
    """从证明反推根，用于调试与审计展示。"""
    h = str(leaf)
    for step in proof:
        s = step["hash"]
        h = _pair(h, s) if step["side"] == "right" else _pair(s, h)
    return h
