"""a2n-node —— 自持节点：**没有服务器、没有托管**时的那份网络。

用途一句话：两个人各自跑一个进程，就能互相发现、互相调用、互相拿到对方签名的
凭据 —— 中间不需要任何第三方。

    from a2n_node.home import use_home        # ① 先定库（零依赖，必须在最前）
    home = use_home("data/nodes/alice")
    from a2n_node import SovereignNode        # ② 再 import（这时才碰 a2n-store）

    node = SovereignNode(name="alice", skills=["ocr-pro"], home=home,
                         executor={"ocr-pro": lambda p: {"text": p["text"].upper()}})
    node.start()
    card, why = node.find("ocr-pro")          # 发现 → 取卡 → 验卡 → 核对哈希
    out = node.call("ocr-pro", {"text": "hi"}, card=card)

**为什么要"先定库再 import"**：a2n-store 在 import 时读环境变量 A2N_DB 并缓存连接。
库路径一旦 import 就定死。所以顺序不是风格问题，是正确性问题（见 a2n_node.home）。

**本包的延迟导入是刻意的**：`__init__` 只 import 零依赖的 home，
SovereignNode 等对象在真正用到时才加载 —— 否则一句
`from a2n_node import SovereignNode` 就会在 use_home 之前把 a2n-store 拉进来，
上面那套顺序约定立刻失效。
"""
from .home import (card_path, db_path_of, identity_path, read_json, same_path,
                   use_home, write_json)

_EXPORTS = {
    # 节点本体
    "SovereignNode": ("node", "SovereignNode"),
    "CallOutcome": ("node", "CallOutcome"),
    "wait_for_http": ("node", "wait_for_http"),
    "FREE": ("node", "FREE"), "BILATERAL": ("node", "BILATERAL"),
    # 卡片自证
    "build_card": ("card", "build_card"), "verify_card": ("card", "verify_card"),
    "card_hash": ("card", "card_hash"), "card_did": ("card", "card_did"),
    "card_endpoint": ("card", "card_endpoint"),
    "card_skills": ("card", "card_skills"), "card_body": ("card", "card_body"),
    "dump_card": ("card", "dump_card"), "did_from_pub": ("card", "did_from_pub"),
    # 双边互签收据。左边是对外的名字，右边是模块里的短名 ——
    # 对外加 "receipt" 后缀是为了让 `verify_receipt` 不会和 peer 的 verify_response 混淆；
    # 模块内保持短名（receipt.sign / receipt.verify）读起来才顺。
    "sign_receipt": ("receipt", "sign"), "verify_receipt": ("receipt", "verify"),
    "ack_receipt": ("receipt", "ack"), "verify_ack": ("receipt", "verify_ack"),
    "hash_payload": ("receipt", "hash_payload"),
}
_SUBMODULES = ("card", "receipt", "peer", "node")


def __getattr__(name: str):
    import importlib

    if name in _SUBMODULES:
        mod = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = mod
        return mod
    where = _EXPORTS.get(name)
    if where:
        modname, attr = where
        mod = importlib.import_module(f"{__name__}.{modname}")
        obj = getattr(mod, attr)
        globals()[name] = obj
        return obj
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["use_home", "db_path_of", "identity_path", "card_path", "read_json",
           "write_json", "SovereignNode", "CallOutcome", "wait_for_http",
           "FREE", "BILATERAL", "build_card", "verify_card", "card_hash",
           "card_did", "card_endpoint", "card_skills", "card_body", "dump_card",
           "did_from_pub", "sign_receipt", "verify_receipt", "ack_receipt",
           "verify_ack", "hash_payload", "card", "receipt", "peer", "node"]
