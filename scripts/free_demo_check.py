"""Verify the live free fixtures, including one real trial-to-graduated lifecycle.

Creates actual test calls, not synthetic evidence. Run after run_free_demo_agents.py.
"""
import argparse
import json

from a2n_sdk import Client
from run_free_demo_agents import PROFILES

SAMPLES = {
    "trim": {"text": "  第一行  \n\n 第二行 "},
    "deduplicate": {"text": "苹果\n苹果\n香蕉"},
    "paragraphs": {"text": "第一段\n换行\n\n第二段"},
    "json": {"text": '{"名字":"测试","免费":true}'},
    "csv": {"text": 'name,note\nAlice,"hello,world"\nBob,test'},
    "links": {"text": "资料 https://example.com https://example.com https://openai.com。"},
}


def verify_free_call(client, agent, profile):
    out = client.call_agent(agent["agent_id"], profile[2], SAMPLES[profile[0]])
    assert out.get("ok") and out.get("state") == "ACCEPTED", out
    assert out.get("settle", {}).get("kind") == "none", out
    assert out.get("result") == profile[6](SAMPLES[profile[0]]), out
    print(f"✓ {agent['name']}：真实结果正确，结算 kind=none", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8011")
    args = parser.parse_args()
    client = Client(args.base, principal="acct:free-demo-check")
    assert client._req("GET", "/v1/pay-methods") == []
    assert client._req("GET", "/v1/party-accounts") == []
    agents = client._req("GET", "/v1/registry/agents?scope=all")
    fixtures = []
    for profile in PROFILES:
        agent = next(a for a in agents if a["name"] == profile[1])
        assert agent["selfproof"] == "signed"
        assert not json.loads(agent["card_json"])["x-a2n"].get("price_book")
        network = client._req("POST", f"/v1/registry/agents/{agent['agent_id']}/network/ping", {})
        assert network["reachable"] and network["rtt_ms"] is not None, network
        verify_free_call(client, agent, profile)
        fixtures.append(agent)

    # Never edit trial counters in the DB: complete actual executions until graduation,
    # then execute one further call without any payment method or pairing.
    agent, profile = fixtures[0], PROFILES[0]
    for _ in range(11):
        evidence = client._req("GET", f"/v1/agents/{agent['agent_id']}/evidence")
        if not evidence["trial"]["trial"]:
            break
        verify_free_call(client, agent, profile)
    assert not client._req("GET", f"/v1/agents/{agent['agent_id']}/evidence")["trial"]["trial"]
    verify_free_call(client, agent, profile)
    assert client._req("GET", "/v1/pay-methods") == []
    assert client._req("GET", "/v1/party-accounts") == []
    print("✓ 6 个上架、验签、通道测速通过；零支付准备可调用，毕业后仍免费。")


if __name__ == "__main__":
    main()
