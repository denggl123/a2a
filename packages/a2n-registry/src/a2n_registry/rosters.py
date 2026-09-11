"""我的市场列表（服务端镜像）：rosters 表的唯一写入口。

生产形态里"我的市场列表"只存在使用方本地；这里是演示环境的服务端镜像，
让管理台能同屏看到同一份列表。**表 owner = a2n-registry**：
路由层与装配层一律通过本模块读写，不得直写表。

表里容忍两种元素形态（历史原因，读取方需按 isinstance 区分）：
  - str：agent_id（管理台"加入我的列表"流程）
  - dict：p2p 发现结果 {"did", "skills", "found_at", "via", ...}
"""
from __future__ import annotations

import json

from a2n_kernel.hashing import now_iso
from a2n_store import conn


def get(principal_id: str) -> list:
    """读原始列表（元素可能是 agent_id 字符串或 p2p offer 字典）。"""
    row = conn().execute("SELECT roster FROM rosters WHERE principal_id=?",
                         (principal_id,)).fetchone()
    if not row:
        return []
    try:
        items = json.loads(row["roster"])
    except (ValueError, TypeError):
        return []
    return items if isinstance(items, list) else []


def save(principal_id: str, items: list) -> list:
    """整表覆盖写（唯一写入口，调用方不要再拼 SQL）。"""
    conn().execute(
        "INSERT INTO rosters (principal_id, roster, updated_at) VALUES (?,?,?)"
        " ON CONFLICT(principal_id) DO UPDATE SET roster=excluded.roster, updated_at=excluded.updated_at",
        (principal_id, json.dumps(items, ensure_ascii=False), now_iso()),
    )
    conn().commit()
    return items


def add(principal_id: str, agent_id: str) -> list:
    items = get(principal_id)
    if agent_id not in items:
        items.append(agent_id)
        save(principal_id, items)
    return items


def remove(principal_id: str, agent_id: str) -> list:
    items = [a for a in get(principal_id) if a != agent_id]
    save(principal_id, items)
    return items


def merge_p2p_offers(principal_id: str, offers: list[dict], skill: str) -> list[dict]:
    """把一次 P2P 发现的结果并入列表（按 did 去重，已有的保留）。

    返回合并后的**原样列表**（含历史字符串元素）；p2p 发现的追加项
    只落在字典元素上，不动管理台加进来的 agent_id。
    """
    existing = get(principal_id)
    strings: list[str] = []
    by_did: dict[str, dict] = {}
    for x in existing:
        if isinstance(x, dict) and x.get("did"):
            by_did[x["did"]] = x
        elif isinstance(x, str):
            strings.append(x)
    for o in offers:
        did = o.get("did")
        if not did:
            continue
        by_did[did] = {"did": did, "skills": o.get("skills") or [skill],
                       "skill": skill, "found_at": now_iso(), "via": "p2p"}
    items = strings + list(by_did.values())
    save(principal_id, items)
    return items
