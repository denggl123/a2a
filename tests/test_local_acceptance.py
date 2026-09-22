from __future__ import annotations

from a2n_node.acceptance_adapter import DeclaredAcceptance
from a2n_sdk import AgentTarget, CallRequest, CallResponse


def _target(template=None):
    card = {"name": "报告", "url": "http://agent.example/a2a",
            "skills": [{"id": "report"}], "x-a2n": {}}
    if template:
        card["x-a2n"]["acceptance_template"] = template
    return AgentTarget("report-agent", card)


def test_declared_template_is_automatically_evaluated():
    acceptance = DeclaredAcceptance()
    template = {"version": "2", "required_fields": ["title", "rows"]}
    passed = acceptance.evaluate(
        _target(template), CallRequest(),
        CallResponse.success({"title": "月报", "rows": [1]}))
    assert passed["passed"] is True
    assert passed["quality_measured"] is True
    assert passed["template_ref"]["version"] == "2"

    failed = acceptance.evaluate(
        _target(template), CallRequest(),
        CallResponse.success({"title": "月报"}))
    assert failed["passed"] is False
    assert "缺必需字段：rows" in failed["reasons"]


def test_no_template_never_claims_quality_was_measured():
    verdict = DeclaredAcceptance().evaluate(
        _target(), CallRequest(), CallResponse.success({"anything": True}))
    assert verdict == {"passed": True, "policy": "delivery-only",
                       "quality_measured": False, "reasons": []}
