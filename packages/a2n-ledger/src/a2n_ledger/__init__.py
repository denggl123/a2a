"""a2n-ledger（L1 资产）：积分账本。

铁律三在这里落地：
  1. 账本不知道业务 —— 只认 ref_type / ref_id
  2. 没有余额字段   —— 余额由 SUM(delta) 得出，没有"改余额"这个动作
  3. 增发只有一个入口 —— mint/burn 检查调用者身份（唯一白名单 = 持牌方回调）
"""
from .service import Ledger, ensure_account, list_accounts

__all__ = ["Ledger", "ensure_account", "list_accounts"]
