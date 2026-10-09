"""Versioned rating vocabulary shared as data, without ledger dependencies."""

DIMENSIONS_V1 = {"buyer_to_seller": ("quality", "punctual", "communication"),
                 "seller_to_buyer": ("on_spec", "cooperative")}
DIMENSIONS_V2 = {direction: values + ("honoring", "dispute_handling") for direction, values in DIMENSIONS_V1.items()}
NEW_DIMENSIONS = {"honoring", "dispute_handling"}


def dimension_names(direction, version="a2n-feedback/1"):
    if version in {"a2n-feedback/1", "a2n-public-feedback/1"}:
        return DIMENSIONS_V1.get(direction, ())
    if version in {"a2n-feedback/2", "a2n-public-feedback/2"}:
        return DIMENSIONS_V2.get(direction, ())
    return ()


def feedback_version(dimensions):
    return "a2n-feedback/2" if set(dimensions) & NEW_DIMENSIONS else "a2n-feedback/1"
