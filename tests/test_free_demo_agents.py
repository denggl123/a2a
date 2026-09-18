"""Utility fixtures must actually work and must not publish made-up price/evidence."""
import importlib.util
import json
from pathlib import Path

import pytest

from a2n_p2p import Identity
from a2n_p2p.attest import verify_selfproof
from a2n_settlement import is_free

spec = importlib.util.spec_from_file_location(
    "free_demo_agents", Path(__file__).resolve().parents[1] / "scripts/run_free_demo_agents.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


def test_real_utilities():
    assert demo.clean_lines(" a \n\n b ")["text"] == "a\nb"
    assert demo.deduplicate_lines({"text": "a\na\nb"}) == {
        "text": "a\nb", "removed": 1, "mode": "deduplicate"}
    assert demo.tidy_paragraphs(" a\n b\n\n c ")["text"] == "a b\n\nc"
    assert demo.format_json('{"名字":"中文"}')["text"] == '{\n  "名字": "中文"\n}'
    with pytest.raises(ValueError):
        demo.format_json("not json")
    assert demo.preview_csv('name,note\nA,"hello,world"')["rows"] == [["A", "hello,world"]]
    assert demo.extract_links("https://example.com https://example.com https://openai.com。")["links"] == [
        "https://example.com", "https://openai.com"]


@pytest.mark.parametrize("profile", demo.PROFILES, ids=[p[0] for p in demo.PROFILES])
def test_cards_signed_permanently_free_and_restart_stable(profile):
    identity = Identity.generate()
    card = demo.build_card(profile, identity)
    assert verify_selfproof(card)[0]
    assert is_free(card, profile[2])
    assert card == demo.build_card(profile, identity)
    assert "price_book" not in card["x-a2n"]
    assert "trial" not in card["x-a2n"]
    assert "sla" not in card["x-a2n"]
    assert "accepts" not in card
    assert "非模型推理" in card["description"]


def test_similar_offers_have_distinct_selling_points():
    similar = demo.PROFILES[:3]
    assert len({p[2] for p in similar}) == 1
    assert len({p[1] for p in similar}) == 3
    assert len({p[4] for p in similar}) == 3


def test_restart_updates_owned_listing_instead_of_registering_duplicate(monkeypatch):
    card = demo.build_card(demo.PROFILES[0], Identity.generate())
    existing = {"agent_id": "ag_existing", "card_json": json.dumps(card)}
    calls = []
    client = demo.ReusableDemoClient("http://127.0.0.1:8011", principal="acct:free-demo")

    def request(method, path, body=None):
        calls.append((method, path, body))
        return [existing] if path.endswith("scope=mine") else existing

    monkeypatch.setattr(client, "_req", request)
    assert client.register(card)["agent_id"] == "ag_existing"
    assert client.node_id == "ag_existing"
    assert not any(method == "POST" for method, _, _ in calls)
    assert ("PUT", "/v1/registry/agents/ag_existing/card", {"card": card}) in calls
    assert ("PUT", "/v1/registry/agents/ag_existing/listing",
            {"visibility": "public", "discover_limit": 0}) in calls
