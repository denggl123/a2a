"""Strict wire JSON shared by protocol adapters; no business dependencies."""
from __future__ import annotations

import json


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"重复的 JSON 键：{key}")
        out[key] = value
    return out


def _finite(value):
    raise ValueError(f"不接受非有限数值：{value}")


def strict_value(raw):
    return json.loads(raw, object_pairs_hook=_unique, parse_constant=_finite)


def strict_object(raw):
    obj = strict_value(raw)
    if not isinstance(obj, dict):
        raise ValueError("请求必须是 JSON 对象")
    return obj
