"""relay 收口 + 运维区收尾（P2，docs/OPTIMIZATION-PLAN.md §4.2 / §5.1）。

一句话：**使用端界面上再也没有第二条调用入口。**

`/v1/relay/*` 是"只给对等账户的底层原语"，不是给使用方的调用方式：
它不经门禁、不建任务、不验收、不公证，收费语义只有对等账户。
留它只为"点对点打节点自定义路由"这类底层动作，且它自己也在平台凭据之内。
面向"用别人的 agent"的调用一律走 `POST /v1/invoke`（治理链）。

另外把运维区收口到一处：**默认折叠**，里面按"运营 / 审计·凭证"分组。
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from a2n_kernel.hashing import new_id
from a2n_registry import registry

CONSOLE = (Path(__file__).resolve().parent.parent / "packages" / "a2n-server"
           / "src" / "a2n_server" / "web" / "console.html")


def _client() -> TestClient:
    from a2n_server.app import app
    return TestClient(app)


def _paid_card(skill: str) -> dict:
    """有价目表的收费卡（relay 的收费语义只有对等账户，正好用它试门）。"""
    return {
        "name": "paid-" + new_id("")[2:6],
        "version": "1.0.0",
        "url": None,
        "skills": [{"id": skill, "name": skill, "tags": []}],
        "accepts": ["peer_account"],
        "x-a2n": {
            "deployment": {"region": "cn-east-2"},
            "price_book": {skill: {"CNY": {"dimensions": [
                {"key": "call_count", "amount": 5, "per": 1}]}}},
        },
    }


def test_relay_route_refuses_anonymous_requests():
    """没凭据就 401：中继不是谁都能蹭的公网跳板。"""
    skill = "relay-" + new_id("")[2:6]
    aid = registry.register(f"acct:{new_id('')[:6]}", _paid_card(skill))["agent_id"]
    r = _client().post(f"/v1/relay/{aid}/invoke", json={"text": "hi"})
    assert r.status_code == 401 and "凭据" in r.json()["detail"]


def test_relay_door_sends_developers_to_the_governed_entry():
    """走了 relay 这扇门而没有对等账户配对 → 明说"底层原语，请走 /v1/invoke"。

    不写这句提示，开发者会以为"中继能用 = 这就是调用方式"，
    从而绕过门禁/建任务/验收/记账 —— 那正是这条闸要堵的。
    """
    skill = "relay-" + new_id("")[2:6]
    aid = registry.register(f"acct:{new_id('')[:6]}", _paid_card(skill))["agent_id"]
    r = _client().post("/v1/transport/call-token", json={"agent_id": aid, "ttl": 600},
                       headers={"X-Principal": "acct:someone-else"})
    assert r.status_code == 403
    assert "/v1/invoke" in r.json()["detail"]


def test_console_has_no_second_call_entry():
    """控制台里**不许**出现对 /v1/relay 的调用 —— 它只能是"门牌"展示。"""
    html = CONSOLE.read_text(encoding="utf-8")
    assert not re.search(r"""api\(\s*['"`]/v1/relay""", html), \
        "控制台出现了对 /v1/relay 的直接调用：使用端又多了一个入口"
    # 使用端的调用入口只有治理链那一个
    assert "api('/v1/invoke'" in html, "控制台缺少唯一的治理链调用入口 /v1/invoke"
    # 门牌展示仍在（地址投影需要它），但不是可点的调用入口
    assert "/v1/relay/" in html, "门牌展示不见了：地址投影要求对外只发 A2N 门牌号"


def test_console_ops_are_one_collapsed_group():
    """运维类收进一个默认折叠的区，按「运营 / 审计·凭证」分组（§5.1）。"""
    html = CONSOLE.read_text(encoding="utf-8")
    assert re.search(r'id="more"\s+class="hide"', html), "运维区没有默认折叠"
    ops = html[html.index('id="more"'):]
    ops = ops[:ops.index("</div>")]
    for label in ("运营", "审计 / 凭证"):
        assert label in ops, f"运维区分组缺「{label}」"
    for page in ("全网总览", "调用测试", "账本与凭证", "争议仲裁", "批次共识", "AP2 凭证"):
        assert page in ops, f"运维区缺页面「{page}」"
