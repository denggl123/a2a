"""商品身份的稳定性（`docs/VISION.md` §1.2「样品即履历」的前提）。

一份供给的 service_id 只能由 **节点身份 + 逻辑商品身份** 决定，**不许**随描述、
版本、上游地址/端口的改动而变 —— 否则改一版文案就把按 service_id 累积的
试用/样品/信誉打断（2026-09-29 复现：仅改描述即换 ID）。
"""
from __future__ import annotations

import base64
import os

import pytest

from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector
from a2n_sdk.projection import (
    card_identity,
    stable_projection_id,
    stable_service_id,
)

NODE = "did:a2n:ag_identity_test"


def _card(**over):
    card = {
        "name": "短视频成片",
        "description": "把主题与卖点做成一条成片",
        "version": "1.0.0",
        "skills": [{"id": "video-short", "name": "短视频成片"}],
        "url": "http://127.0.0.1:9001/a2a/video",
    }
    card.update(over)
    return card


def test_description_and_version_do_not_change_identity():
    base = _card()
    sid = stable_service_id(NODE, base)
    assert stable_service_id(NODE, _card(description="换一版更顺的说明")) == sid
    assert stable_service_id(NODE, _card(version="2.3.1")) == sid
    assert stable_service_id(NODE, _card(url="http://127.0.0.1:7777/a2a/x")) == sid
    # 投影 id 同口径（买方侧）
    pid = stable_projection_id(NODE, base, "http://seller/a2a/svc")
    assert stable_projection_id(NODE, _card(description="改说明"),
                                "http://seller/a2a/svc") == pid


def test_different_product_gets_a_different_identity():
    a = stable_service_id(NODE, _card())
    b = stable_service_id(NODE, _card(name="游戏设计工坊",
                                      skills=[{"id": "game-design"}]))
    c = stable_service_id(NODE, _card(skills=[{"id": "video-long",
                                               "name": "长视频"}]))
    assert len({a, b, c}) == 3, "不同商品不能撞同一个身份"


def test_uid_wins_and_survives_text_changes():
    card = {"x-a2n": {"uid": "11111111-2222-4333-8444-555555555555"},
            "name": "A", "description": "x", "version": "1",
            "skills": [{"id": "s1"}]}
    sid = stable_service_id(NODE, card)
    changed = {"x-a2n": {"uid": "11111111-2222-4333-8444-555555555555"},
               "name": "A 改名了", "description": "完全不同",
               "version": "9.9", "skills": [{"id": "s1"}]}
    assert stable_service_id(NODE, changed) == sid, "有 uid 时一切文案改动都不换身份"
    assert card_identity(changed).startswith("uid:")


def test_node_identity_still_separates_two_nodes():
    card = _card()
    assert stable_service_id("did:a2n:nodeA", card) != \
        stable_service_id("did:a2n:nodeB", card)


@pytest.fixture
def protector():
    return EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())


def test_buyer_can_refresh_a_stale_import_by_identity(tmp_path, protector):
    """卖方换了地址后，买方按**逻辑商品身份**找回最新卡并原地更新（R0-4）。"""
    daemon = Daemon(tmp_path, port=0, protector=protector).start()
    try:
        old = {"name": "海报工坊", "url": "http://old.example/a2a/svc_old",
               "skills": [{"id": "poster"}], "version": "1.0.0",
               "x-a2n": {"uid": "uid-poster-1"}}
        item = daemon.runtime.import_agent(old)
        pid = item.projection_id
        daemon.management.store.put("projections", pid, {
            "network_card": old, "target_ref": item.target.ref, "projection_id": pid})

        fresh = {"name": "海报工坊（改地址）", "url": "http://new.example/a2a/svc_new",
                 "skills": [{"id": "poster"}], "version": "1.1.0",
                 "x-a2n": {"uid": "uid-poster-1"}}
        daemon.management._search_all = lambda *a, **k: (
            [{"card": fresh, "source": "p2p", "headers": {}}], [], True)
        out = daemon.management.refresh_projection(pid)
        assert out["refreshed"] and out["projection_id"] == pid, "投影 id 不变（工作台 URL 不换）"
        assert out["target_ref"] == "http://new.example/a2a/svc_new"
        assert out["unchanged"] is False

        # 找不到同一商品：如实说找不到，不删收藏、不假装成功
        other = {"name": "别的商品", "url": "http://x/a2a/y",
                 "skills": [{"id": "poster"}], "x-a2n": {"uid": "uid-other"}}
        daemon.management._search_all = lambda *a, **k: (
            [{"card": other, "source": "p2p", "headers": {}}], [], True)
        miss = daemon.management.refresh_projection(pid)
        assert miss["refreshed"] is False and miss["projection_id"] == pid
        assert daemon.runtime.imported.get(pid), "收藏不能被悄悄删掉"

        # 没有任何发现通道：明说没配置
        daemon.management._search_all = lambda *a, **k: ([], [], False)
        none = daemon.management.refresh_projection(pid)
        assert none["refreshed"] is False and "发现通道" in none["reason"]
    finally:
        daemon.stop()
