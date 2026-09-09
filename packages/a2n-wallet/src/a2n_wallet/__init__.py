"""a2n-wallet（L3 业务）：积分出口。

出口只有一条 —— 提现到实名绑定账户，随即销毁积分。
不做代币、不上链记账、不设二级市场。
"""
from .service import MAX_WITHDRAW_PER_DAY, Wallet, wallet

__all__ = ["Wallet", "wallet", "MAX_WITHDRAW_PER_DAY"]
