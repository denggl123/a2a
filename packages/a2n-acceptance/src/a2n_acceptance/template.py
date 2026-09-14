"""模板偏差（质量硬指标）：交付物与"上架时声明的验收模板"比出来的偏差。

模板是**要求**，不是**答案** —— 它只衡量"有没有满足你声明的要求"，不衡量
"结果好不好"。所以它和主观评分**不能互相替代，更不能合成一个总分**。

三个可复算的分量：

    d_结构 = 缺失或多余的项数 ÷ 模板项数
    d_完整 = 应有内容项里没覆盖的比例
    d_内容 = 有参考时的一致性距离（无参考时为 0，并明确标注"无参考"）

    总偏差 D = w1·d_结构 + w2·d_完整 + w3·d_内容     ← 权重随模板版本化并公开
    质量分  = 100 × (1 − D)

三条铁律：
  1. 模板与权重**上架时声明、公开可见、改了必须升版本**；历史案例用当时的模板
     版本重算（与"分账规则版本化"同一条纪律，两年后要能重放）。
  2. 偏差**可复算**：任何人拿模板 + 交付物都能算出同一个数 —— 否则它不叫硬指标。
  3. **无模板的能力不产生偏差指标**（界面显示"未声明验收模板"）。
     **绝不允许伪造一个 0 偏差** —— 那等于把"没测"说成"满分"。
"""
from __future__ import annotations

from typing import Any

# 默认权重：结构 / 完整度 / 内容。无参考时内容项权重归零、另两段重归一化。
DEFAULT_WEIGHTS = {"struct": 0.3, "completeness": 0.4, "content": 0.3}

TEMPLATE_KEYS = ("version", "required_fields", "required_content", "reference", "tolerance")


def parse_template(card: dict) -> dict | None:
    """从卡里取出验收模板（`x-a2n.acceptance_template`）。

    没声明 → None。**不猜、不兜底一个空模板** —— "没声明"与"声明了但要求为空"
    是两回事，前者不产生质量指标。
    """
    t = ((card or {}).get("x-a2n") or {}).get("acceptance_template")
    if not isinstance(t, dict):
        return None
    fields = t.get("required_fields")
    contents = t.get("required_content")
    ref = t.get("reference")
    if not fields and not contents and not ref:
        return None                      # 声明了但什么都没有 = 不产生指标
    norm = {
        "version": str(t.get("version") or "1"),
        "required_fields": [str(f) for f in (fields or []) if str(f).strip()],
        "required_content": [c for c in (contents or []) if isinstance(c, dict) and c.get("key")],
        "reference": ref if isinstance(ref, dict) and ref.get("path") else None,
        "tolerance": float(t.get("tolerance") if isinstance(t.get("tolerance"), (int, float)) else 0.1),
    }
    weights = t.get("weights")
    if isinstance(weights, dict):
        norm["weights"] = {k: float(v) for k, v in weights.items() if k in DEFAULT_WEIGHTS}
    return norm


def template_ref(template: dict) -> dict:
    """模板口径快照（随报告一起存档，历史案例据此可重放）。"""
    return {"key": "acceptance.template", "version": template["version"],
            "weights": template.get("weights") or DEFAULT_WEIGHTS}


def _dig(delivery: Any, path: str) -> Any:
    """按点号路径取值（delivery 是 dict 时）。取不到返回 None。"""
    cur = delivery
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _content_distance(item: dict, delivery: Any, tolerance: float) -> float:
    """一个内容项的覆盖距离：0 = 覆盖，1 = 未覆盖。"""
    val = _dig(delivery, item.get("path") or "")
    mode = item.get("mode") or "present"
    if mode == "present":
        return 0.0 if val not in (None, "", [], {}) else 1.0
    if mode == "contains":
        want = str(item.get("value") or "")
        return 0.0 if want and want in str(val or "") else 1.0
    if mode == "equals":
        want = item.get("value")
        if isinstance(want, (int, float)) and isinstance(val, (int, float)):
            span = abs(float(want)) or 1.0
            return 0.0 if abs(float(val) - float(want)) / span <= tolerance else 1.0
        return 0.0 if val == want else 1.0
    if mode in ("min", "max"):
        want = item.get("value")
        if not isinstance(val, (int, float)) or not isinstance(want, (int, float)):
            return 1.0
        ok = val >= want if mode == "min" else val <= want
        return 0.0 if ok else 1.0
    return 1.0                            # 未知 mode = 无法核对 = 记未覆盖（不假装通过）


def deviation(template: dict, delivery: Any, *, weights: dict | None = None) -> dict[str, Any]:
    """交付物 vs 模板 → 三个分量 + 总偏差 D + 质量分。

    纯函数、无副作用、无时间依赖 —— 所以任何一个第三方拿同样的素材
    都能算出同一个数。这是它敢叫"硬指标"的全部理由。
    """
    reasons: list[str] = []
    w = {**DEFAULT_WEIGHTS, **(template.get("weights") or {}), **(weights or {})}

    # ① 结构：缺一个记一次；多一个也记（多余字段同样是"与模板不符"）
    want_fields = list(template.get("required_fields") or [])
    if want_fields:
        if isinstance(delivery, dict):
            have = set(delivery)
            missing = [f for f in want_fields if f not in have]
            extra = sorted(set(have) - set(want_fields))
            d_struct = (len(missing) + len(extra)) / len(want_fields)
            for f in missing:
                reasons.append(f"缺必需字段：{f}")
            for f in extra:
                reasons.append(f"多出模板未声明的字段：{f}")
        else:
            d_struct = 1.0
            reasons.append("交付物不是结构化对象，无法核对必需的字段结构")
    else:
        d_struct = 0.0

    # ② 完整度：应有的内容项没覆盖的比例
    items = list(template.get("required_content") or [])
    if items:
        tol = float(template.get("tolerance") or 0.1)
        uncovered = [it for it in items if _content_distance(it, delivery, tol) > 0]
        d_completeness = len(uncovered) / len(items)
        for it in uncovered:
            reasons.append(f"未覆盖必需内容项：{it.get('key')}")
    else:
        d_completeness = 0.0

    # ③ 内容一致性：有参考才算；**无参考时明确标注，且权重归零并重归一化**
    ref = template.get("reference")
    no_reference = not ref
    if no_reference:
        d_content = 0.0
        w = {"struct": w["struct"], "completeness": w["completeness"], "content": 0.0}
        reasons.append("无参考：内容一致性未计算（不是 0 偏差，是没测）")
    else:
        tol = float(template.get("tolerance") or 0.1)
        d_content = _content_distance({"path": ref["path"], "mode": ref.get("mode") or "equals",
                                       "value": ref.get("value")}, delivery, tol)
        if d_content > 0:
            reasons.append(f"与参考不一致：{ref['path']}")

    total_w = w["struct"] + w["completeness"] + w["content"]
    if total_w <= 0:
        total_w = 1.0
    d = (w["struct"] * d_struct + w["completeness"] * d_completeness
         + w["content"] * d_content) / total_w
    d = max(0.0, min(1.0, d))
    tolerance = float(template.get("tolerance") or 0.1)
    return {
        "d_struct": round(d_struct, 6),
        "d_completeness": round(d_completeness, 6),
        "d_content": round(d_content, 6),
        "D": round(d, 6),
        "quality": round(100.0 * (1.0 - d), 4),
        "no_reference": no_reference,
        # 超容差本身不让验收失败（偏差是"质量"，不是"无效"），但必须显式标出来
        "over_tolerance": bool(ref or want_fields or items) and d > tolerance,
        "tolerance": tolerance,
        "weights": w,
        "template_version": template["version"],
        "reasons": reasons,
    }
