"""跨包契约小工具：字段集校验（鸭子契约无继承时也能在 register 期发现不齐）。"""
from __future__ import annotations

from typing import Iterable, Mapping


def attrs(obj: object, names: Iterable[str]) -> bool:
    """对象是否同时具备 ``names`` 全部命名成员（属性或 dict 键均可）。

    给 ``Protocol`` + ``attrs`` 的鸭子组合用：Protocol 声明契约，
    register 时 ``attrs(guide, ("channel","label",...))`` 一行验形态。
    Mapping（如 dict）按 key 校验，其他按 ``hasattr``。
    """
    if isinstance(obj, Mapping):
        keys = set(obj.keys())
        return all(n in keys for n in names)
    return all(hasattr(obj, n) for n in names)