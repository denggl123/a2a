"""Pure, reproducible acceptance policies. State belongs to the node runtime."""
from .service import (VARIANCE_TOLERANCE, BaselineSamplePolicy, judge, policy_ref, set_policy)
from .template import DEFAULT_WEIGHTS, deviation, parse_template, template_ref
__all__ = ["judge", "policy_ref", "set_policy", "BaselineSamplePolicy", "VARIANCE_TOLERANCE",
           "parse_template", "deviation", "template_ref", "DEFAULT_WEIGHTS"]
