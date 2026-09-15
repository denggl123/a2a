"""a2n-reputation（L2 网络）：信誉分与使用评价。

为什么单独成包：发现时六维都可见，但**排序只能用它**——
只有信誉是网络验证过的事实，其余维度都是节点自己说的（price_hint 尤其不参与排序）。

`ratings` 是"使用端真实评估质量"的落点：绑定具体交付的评分 + 归一化（消掉手松/手紧）
+ 最小样本门（案例先行、分数后到）+ 同源不计入。硬指标（模板偏差）不在这里，
在 a2n-acceptance —— **两者分开呈现，绝不合成一个总分**。
"""
from .ratings import (K_SHRINK, MIN_NON_SELF_CASES, PUBLISH_MIN_RATER, PUBLISH_MIN_RATERS,
                      SLOPE_MAX, SLOPE_MIN, card_gaps, counts, graduate_blockers,
                      list_for_agent, normalize_score, rate, rater_stats,
                      ratings_for_tasks, summary)
from .service import DefaultWeightedModel, apply_event

__all__ = ["apply_event", "DefaultWeightedModel",
           "rate", "summary", "counts", "rater_stats", "normalize_score",
           "list_for_agent", "ratings_for_tasks", "card_gaps", "graduate_blockers",
           "K_SHRINK", "PUBLISH_MIN_RATER", "PUBLISH_MIN_RATERS", "MIN_NON_SELF_CASES",
           "SLOPE_MIN", "SLOPE_MAX"]
