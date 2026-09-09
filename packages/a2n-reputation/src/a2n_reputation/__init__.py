"""a2n-reputation（L2 网络）：信誉分。

为什么单独成包：发现时六维都可见，但**排序只能用它**——
只有信誉是网络验证过的事实，其余维度都是节点自己说的（price_hint 尤其不参与排序）。
"""
from .service import DefaultWeightedModel, apply_event

__all__ = ["apply_event", "DefaultWeightedModel"]
