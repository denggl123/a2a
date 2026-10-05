"""Local quality facts, separated from network reachability and unverified claims."""
def quality_facts(store, feedback, *, scope=""):
    rows = store.outcomes(scope=scope, limit=100)
    outcomes = [r.get("outcome") or {} for r in rows]
    measured = [o for o in outcomes if (o.get("verdict") or {}).get("quality_measured") is True]
    return {"scope": scope, "sample_count": len(rows), "window": "latest_100_calls",
            "completed": sum(o.get("state") == "COMPLETED" for o in outcomes),
            "quality_measured": len(measured),
            "quality_passed": sum((o.get("verdict") or {}).get("passed") is True for o in measured),
            "network_unknown": sum(o.get("state") in {"TIMEOUT", "DELIVERY_UNKNOWN", "INTERRUPTED"} for o in outcomes),
            "feedback": feedback.summary(), "ranking_score": None,
            "notice": "事实来自本机记录；网络失败不代表质量差，反馈不自动裁定或扣款。"}
