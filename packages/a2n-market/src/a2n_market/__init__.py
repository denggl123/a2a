"""a2n-market（L4 支撑）：行情板。

只发行情，不定价：公开的是统计事实（近期成交均价、供给密度、信誉构成），
不是报价，也不做推荐 —— 搜索可以中性，推荐永远不中性。

挂牌价从 a2n-settlement.price 读（价目表的单一来源），本包不再解析一遍
卡里的价格结构 —— 两处解析就会有两种口径。
"""
from .service import board, stats

__all__ = ["stats", "board"]
