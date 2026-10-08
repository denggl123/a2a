"""Immutable encrypted channel versions used by existing payment plans."""
from dataclasses import dataclass

from a2n_sdk.trade_facts import digest
from .evm_payment import EvmNativeDriver, METHOD
from .x402.evm import EvmSigner
from .x402.http import HTTPClient
from .x402.service import X402NodeService
from .x402.refund import X402RefundDriver


@dataclass
class ChannelVersion:
    channel_id: str
    config: dict
    signer: object
    http: object
    native: list
    x402: object
    gate: object
    refund_driver: object

    @classmethod
    def build(cls, store, config, signer=None, *, x402=None, drivers=None):
        drivers = drivers or {}
        http = HTTPClient(allow_http=config.get("allow_http", False))
        native = [drivers.get(METHOD + ":" + digest([c, signer.address if signer else None]))
                  or EvmNativeDriver(store, c, signer) for c in config.get("native", [])]
        if len({(d.config["network"], d.config["currency"]) for d in native}) != len(native):
            raise ValueError("DUPLICATE_NATIVE_PAYMENT_ASSET")
        if x402 is None:
            cfg = config.get("x402")
            existing = drivers.get("x402-v2-eip3009:" + digest([cfg, signer.address if signer else None])) if cfg else None
            x402 = X402NodeService(store, None if existing else cfg, signer)
            if existing:
                x402.driver, x402.config = existing, cfg
        facilitator = config.get("facilitator_url")
        gate = x402.resource_server(facilitator) if facilitator else None
        refund = ((drivers.get("eip3009-refund/1:" + digest([x402.driver.driver_id, facilitator]))
                   or X402RefundDriver(store, x402.driver, facilitator)) if gate else None)
        return cls("pc_" + digest([config, signer.address if signer else None]),
            dict(config), signer, http, native, x402, gate, refund)

    def register(self, payments):
        for driver in [*self.native, *([self.x402.driver] if self.x402.driver else []),
                       *([self.refund_driver] if self.refund_driver else [])]:
            payments.register(driver)
        self.x402.bind(payments)

    def persist(self, store):
        # LocalStore encrypts the entire value. Keys never enter API summaries.
        key = "0x" + bytes(self.signer._account.key).hex() if self.signer else None
        store.put("payment_channel_versions", self.channel_id, {"config": self.config, "wallet_key": key})

    @classmethod
    def restore(cls, store, row, *, drivers=None):
        signer = EvmSigner(row["wallet_key"]) if row.get("wallet_key") else None
        return cls.build(store, row["config"], signer, drivers=drivers)
