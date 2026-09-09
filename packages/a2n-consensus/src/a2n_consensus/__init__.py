"""a2n-consensus（L4 共识层）：把"我的账"变成"我们的账"。

a2n-p2p 回答了前三个网络问题（我是谁 / 我怎么找到别人 / 消息怎么到），
第四个问题在这里：**我们怎么对"发生过什么"达成一致**。

设计前提：A2N 没有中心服务器，所以"账本是对的"这句话不能由任何人单方面宣布。
我们采用**锚定共识**（anchored consensus），而不是 PoW / PoS：

  1. 一个 epoch（纪元）把区间内所有账目压成一个 **Merkle 根**；
  2. 不少于 **2/3 权重**的见证人（witness）对这个根签名；
  3. 持牌托管方对这个根的**资金锚**（escrow balance attestation）签名。

三把钥匙分别落在三方手里：
  账本批次（节点） / 见证签名（网络） / 资金锚（持牌方）。
**没有任何一方能独自凑齐三把钥匙** —— 这就是铁律三"廉洁刻进代码"
在去中心化场景下的升级形态：想凭空增发，需要同时收买节点、网络与持牌方。

本包刻意不知道账本长什么样：批次由装配层通过 `set_batch_provider()` 注入，
签名算法通过 `set_verifier()` 注入。共识不该绑死存储，也不该绑死某种密码学库。
"""
from .anchor import Anchor, AnchorAttestation, EscrowAttestation
from .epoch import Epoch, EpochService, consensus
from .witness import Witness, WitnessSet

__all__ = ["Epoch", "EpochService", "consensus", "Witness", "WitnessSet",
           "Anchor", "AnchorAttestation", "EscrowAttestation"]
