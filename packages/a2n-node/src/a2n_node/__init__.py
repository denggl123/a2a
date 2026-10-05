"""A complete sovereign platform per node, assembled exclusively by Daemon."""
from .home import use_home, same_path, db_path_of
_EXPORTS = {
    "Daemon": ("daemon", "Daemon"),
    "P2PDiscoveryService": ("p2p_service", "P2PDiscoveryService"),
    **{name: ("card", name) for name in ("build_card", "verify_card", "card_hash", "card_did",
        "card_endpoint", "card_skills", "card_body", "dump_card", "did_from_pub")},
    "sign_receipt": ("receipt", "sign"), "verify_receipt": ("receipt", "verify"),
    "ack_receipt": ("receipt", "ack"), "verify_ack": ("receipt", "verify_ack"),
    "hash_payload": ("receipt", "hash_payload"),
}
_SUBMODULES = ("card", "receipt", "peer", "p2p_service")
def __getattr__(name):
    import importlib
    if name in _SUBMODULES:
        result = importlib.import_module(f"{__name__}.{name}")
    elif name in _EXPORTS:
        module, attr = _EXPORTS[name]
        result = getattr(importlib.import_module(f"{__name__}.{module}"), attr)
    else:
        raise AttributeError(name)
    globals()[name] = result
    return result
__all__ = ["use_home", "same_path", "db_path_of", *_EXPORTS, *_SUBMODULES]
