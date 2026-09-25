"""a2n-p2p —— A2N 网络层（L0）。

项目三层模型：
    L2 服务层   A2A 服务本身
    L1 管理层   注册 / 发现 / 调度 / 验收 / 信誉 / KYA
    L0 网络层   身份 / 寻址 / 连通 / 传播 / 共识      ← 本包

本包只回答网络层的四个问题中的三个：

    Q1 我是谁         → identity.Identity   ed25519，did:a2n:ag_<指纹>
    Q2 我怎么找到别人 → node.P2PNode        邻居发现（广播 beacon / bootstrap / gossip 交换）
    Q3 消息怎么到     → envelope.Envelope   去重 + TTL + 签名三件套

Q4（共识）不在本包，由 a2n-consensus 负责——共识需要知道账本，而本包不知道。

一条边界：**大负载不走 gossip**。签名 UDP 信封限制在 MTU 安全范围，完整卡与任务
由传输层的 HTTP/QUIC/隧道处理，
让 gossip 扛大包是层次错位。

典型用法：

    from a2n_p2p import Identity, P2PNode
    me = Identity.generate()
    net = P2PNode(me, port=9701).start()
    net.announce(["ocr-pro"])
    offers = net.query("ocr-pro", timeout=2.0)
"""
from .attest import (card_body, card_did, card_pub_raw, did_from_pub,
                     metering_payload, pub_b64, pub_unb64, sign_metering,
                     verify_metering, verify_selfproof)
from .envelope import (ANCHOR, CARD, EPOCH, HELLO, LEDGER_TYPES, MSG_TYPES,
                       NETWORK_TYPES, OFFER, PING, PONG, PUNCH, PUNCH_HINT,
                       PUNCH_REQ, PUNCH_TYPES, QUERY, RECEIPT, RESULT, TASK,
                       Envelope, hello_payload, parse_pub, verify_pub)
from .identity import DID_PREFIX, Identity, fingerprint_of
from .node import P2PNode
from .node import MAX_GOSSIP_BYTES
from .peers import Peer, PeerTable
from .signer import Signer, verifier_fn, verify

__all__ = [
    "Identity", "P2PNode", "PeerTable", "Peer", "Envelope", "Signer",
    "MAX_GOSSIP_BYTES",
    "hello_payload", "parse_pub", "DID_PREFIX", "verify", "verifier_fn",
    "verify_pub", "fingerprint_of",
    "did_from_pub", "card_pub_raw", "card_did", "metering_payload",
    "sign_metering", "verify_metering", "pub_b64", "pub_unb64",
    "card_body", "verify_selfproof",
    "MSG_TYPES", "LEDGER_TYPES", "NETWORK_TYPES", "PUNCH_TYPES",
    "CARD", "QUERY", "OFFER", "TASK", "RESULT", "RECEIPT", "EPOCH", "ANCHOR",
    "HELLO", "PING", "PONG", "PUNCH_REQ", "PUNCH_HINT", "PUNCH",
]
