"""a2n-ap2 测试：授权链校验 / 预算翻译 / 结算凭证 / 问责材料。"""
from __future__ import annotations

import pytest

from a2n_ap2 import (AccountabilityPack, BudgetTranslator, Mandate,
                     MandateError, ReceiptBuilder, new_cart, new_payment,
                     validate_chain)
from a2n_p2p import Signer, verifier_fn


def _sig(m: Mandate, signer: Signer) -> Mandate:
    m.pub = signer.pub
    m.sig = signer.sign(m.msg())
    return m


def _chain() -> tuple[Mandate, Mandate, Mandate]:
    intent = Mandate(kind="intent", subject="acct:alice", agent="ag:shopper",
                     issuer="acct:alice",
                     scope={"max_amount_fen": 10000, "skill": "shop"},
                     expires_at="2099-12-31T23:59:59Z")
    cart = new_cart(intent, [{"item": "ocr", "amount_fen": 3000}], issuer="ag:shopper")
    payment = new_payment(cart, issuer="acct:alice", amount_fen=3000)
    return intent, cart, payment


# ---------- 授权链 ----------

def test_valid_chain_passes():
    intent, cart, payment = _chain()
    rep = validate_chain(payment, cart, intent)
    assert rep["ok"] and rep["amount_fen"] == 3000


def test_chain_with_signatures():
    signer = Signer.generate()
    intent, cart, payment = _chain()
    for m in (intent, cart, payment):
        _sig(m, signer)
    rep = validate_chain(payment, cart, intent, verifier=verifier_fn())
    assert rep["signatures"] == {"intent": True, "cart": True, "payment": True}


def test_chain_amount_over_limit_rejected():
    intent, cart, payment = _chain()
    payment.scope["amount_fen"] = 99999      # 支付授权超出购物车总价：想多扣
    with pytest.raises(MandateError):
        validate_chain(payment, cart, intent)
    cart.scope["total_fen"] = 99999          # 购物车涨价超出授权：想多花
    with pytest.raises(MandateError, match="上限"):
        validate_chain(payment, cart, intent)


def test_chain_broken_reference_rejected():
    intent, cart, payment = _chain()
    payment.parent = "md_someone_else"
    with pytest.raises(MandateError, match="未引用"):
        validate_chain(payment, cart, intent)


def test_chain_subject_mismatch_rejected():
    intent, cart, payment = _chain()
    payment.subject = "acct:eve"
    with pytest.raises(MandateError, match="主体不一致"):
        validate_chain(payment, cart, intent)


def test_chain_expired_rejected():
    intent, cart, payment = _chain()
    intent.expires_at = "2020-01-01T00:00:00Z"
    with pytest.raises(MandateError, match="过期"):
        validate_chain(payment, cart, intent)


def test_invalid_signature_rejected():
    signer, other = Signer.generate(), Signer.generate()
    intent, cart, payment = _chain()
    _sig(intent, signer)
    _sig(cart, signer)
    # payment 声称是 signer 签的，但签名实际出自 other 的私钥
    payment.pub = signer.pub
    payment.sig = other.sign(payment.msg())
    with pytest.raises(MandateError, match="签名无效"):
        validate_chain(payment, cart, intent, verifier=verifier_fn())


def test_mandate_requires_parent():
    with pytest.raises(MandateError):
        Mandate(kind="cart", subject="a", agent="b", issuer="c")


def test_unknown_kind_rejected():
    with pytest.raises(MandateError, match="未知"):
        Mandate(kind="coupon", subject="a", agent="b", issuer="c")


# ---------- 扩展一：预算翻译 ----------

def test_budget_translation_freezes_not_promises():
    intent = Mandate(kind="intent", subject="acct:alice", agent="ag:x", issuer="acct:alice",
                     scope={"max_amount_fen": 5000}, expires_at="2099-01-01T00:00:00Z")
    b = BudgetTranslator.from_intent(intent, "ocr-pro", price_hint_fen=3000)
    assert b["budget"] == 3000          # 1 积分 = 1 分，无换算
    assert b["frozen"] is True
    assert b["mandate_ref"] == intent.id
    # 授权不够时按授权冻结：授权 100 分就只冻 100 积分
    intent.scope["max_amount_fen"] = 100
    assert BudgetTranslator.from_intent(intent, "ocr-pro", price_hint_fen=3000)["budget"] == 100


def test_budget_translation_requires_cap():
    intent = Mandate(kind="intent", subject="a", agent="b", issuer="a", scope={})
    with pytest.raises(MandateError, match="上限"):
        BudgetTranslator.from_intent(intent, "ocr-pro")


def test_budget_translation_requires_intent():
    cart = Mandate(kind="cart", subject="a", agent="b", issuer="c", parent="i")
    with pytest.raises(MandateError, match="intent"):
        BudgetTranslator.from_intent(cart, "ocr-pro")


# ---------- 扩展二：结算凭证 ----------

def test_receipt_contains_four_evidences():
    task = {"id": "t1", "skill_id": "ocr-pro", "node_id": "ag:n", "result_hash": "rh",
            "amount": 33, "state": "SETTLED"}
    settlement = {"amount": 33, "splits": {"node": 30, "author": 1, "fee": 1, "pool": 1},
                  "rule_ref": {"key": "split.fixed", "version": "2026.09.01"}}
    r = ReceiptBuilder.build(task, settlement, notary_rid="rc1", epoch_id="ep1",
                             usage={"call_count": 33})
    assert r["result_hash"] == "rh"                 # 干了什么
    assert r["usage"] == {"call_count": 33}         # 干了多少
    assert r["splits"]["node"] == 30                # 给了谁
    assert r["notary_rid"] == "rc1" and r["epoch_id"] == "ep1"   # 谁证明的
    assert r["amount_fen"] == 33                    # 积分↔分 1:1


def test_receipt_digest_changes_with_content():
    task = {"id": "t1", "skill_id": "s", "node_id": "n", "result_hash": "rh",
            "amount": 1, "state": "SETTLED"}
    r1 = ReceiptBuilder.build(task, {"amount": 1, "splits": {}})
    task2 = {**task, "amount": 2}
    r2 = ReceiptBuilder.build(task2, {"amount": 2, "splits": {}})
    assert ReceiptBuilder.digest(r1) != ReceiptBuilder.digest(r2)


# ---------- 扩展三：问责材料 ----------

def test_accountability_pack_freezes_both_sides():
    task = {"id": "t1", "node_id": "n", "requester_id": "u", "result_hash": "rh"}
    ur = {"dims": '{"call_count": 3}', "observed": '{"wall_time_ms": 900}',
          "variance": 120.0, "contract": '{"unit_prices": {}}'}
    pack = AccountabilityPack.build(task, ur, reputation=0.8, dispute_id="ds1",
                                    notary_rid="rc9")
    assert pack["reported_dims"] == {"call_count": 3}
    assert pack["observed_dims"] == {"wall_time_ms": 900}
    assert pack["variance"] == 120.0
    assert pack["reputation_at_call"] == 0.8
    assert pack["dispute_id"] == "ds1"
