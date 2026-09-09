"""a2n-transport（L2 网络）：把 p2p 穿透、反向长连接、中继转发收进同一套协商。

    direct / holepunch / tunnel / relay / pull —— 先列全候选，再选优（ICE 思路）

三条边界：打洞只做候选永不做依赖；数据可走直连但证据必须走平台；
中继是唯一 100% 保证连通的通道。
"""
from .hub import FORWARD_TIMEOUT_S, LADDER, Tunnel, TunnelHub, hub, negotiate

__all__ = ["Tunnel", "TunnelHub", "hub", "negotiate", "LADDER", "FORWARD_TIMEOUT_S"]
