"""a2n-notary（L4 支撑）：刻章。

纯订阅者：它挂掉不影响任何业务，只影响"章还没刻"，补刻即可 ——
这正是公证层应有的失败语义。
"""
from .service import Notary, notary

__all__ = ["Notary", "notary"]
