"""Node-side automatic acceptance using A2N's single template implementation."""
from __future__ import annotations

from a2n_acceptance.template import deviation, parse_template, template_ref


class DeclaredAcceptance:
    """Evaluate only the objective contract declared in an Agent Card.

    No template means no quality claim: successful delivery is usable, but the
    verdict explicitly says quality was not measured.  This is deliberately
    different from inventing a perfect score for an unmeasured result.
    """

    def evaluate(self, target, request, response) -> dict:
        if not response.ok:
            return {"passed": False, "policy": "delivery",
                    "quality_measured": False,
                    "reasons": [str(response.error or "对端未交付")]}
        template = parse_template(target.card)
        if not template:
            return {"passed": True, "policy": "delivery-only",
                    "quality_measured": False, "reasons": []}
        measured = deviation(template, response.result)
        return {
            "passed": bool(measured.get("hard_passed")),
            "policy": "declared-template",
            "quality_measured": True,
            "quality": measured.get("quality"),
            "deviation": measured,
            "template_ref": template_ref(template),
            "reasons": list(measured.get("hard_failures") or []),
        }
