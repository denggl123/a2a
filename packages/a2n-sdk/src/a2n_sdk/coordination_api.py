"""Local coordination HTTP translation; authorization belongs to the gateway."""
from __future__ import annotations

import re

from .coordination import CandidateKey
from .coordination_service import SearchError, parse_spec


def dispatch(service, method, path, query, body, headers):
    if service is None:
        raise SearchError("NOT_FOUND", "节点未装配协调服务")
    command_id = headers.get("Idempotency-Key", "")
    if path == "/v1/coord/searches" and method == "POST":
        return 201, service.start(parse_spec(body), command_id).to_dict()
    parts = path.split("/")
    if len(parts) not in (5, 6) or parts[1:4] != ["v1", "coord", "searches"]:
        raise SearchError("NOT_FOUND", "协调接口不存在")
    sid = parts[4]
    if method == "GET":
        if len(parts) == 5:
            return 200, service.get(sid).to_dict()
        if parts[5] == "candidates":
            return 200, service.candidates(sid, (query.get("result_cursor") or [""])[0],
                                           int((query.get("limit") or ["20"])[0]))
    if method == "POST" and len(parts) == 6:
        match = re.fullmatch(r'"([0-9]+)"', headers.get("If-Match", ""))
        revision = int(match[1]) if match else None
        if parts[5] == "route-plan":
            if not isinstance(body.get("probe", False), bool):
                raise ValueError("probe 必须是布尔值")
            return 200, service.plan(sid, CandidateKey.from_dict(body.get("key") or {}),
                                      command_id, revision, body.get("probe", False)).to_dict()
        return 200, service.control(sid, parts[5], command_id, revision, body)
    raise SearchError("NOT_FOUND", "协调接口不存在")
