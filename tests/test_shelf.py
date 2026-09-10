"""货架 SDK：自动化 agent（hermes / codex / claude code）的程序化上架。

不发真实请求：拦截 _req 检查端点与参数；CLI 直调 main() 断言退出码与输出。
card 的结构合法性（uid/价目/描述）在这里保证——调用方只给关键字段。
"""
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from a2n_sdk import Client, auto_desc, build_card, from_card, shelf, update_card
from a2n_sdk.__main__ import main


class Recording(Client):
    def __init__(self) -> None:
        super().__init__("http://x", principal="me")
        self.calls: list[tuple] = []

    def _req(self, method, path, body=None, headers=None):
        self.calls.append((method, path, body, headers))
        if path == "/v1/registry/agents" and method == "POST":
            return {"agent_id": "ag_new", "card_hash": "h", "status": "PROBATION",
                    "kya_grade": "B", "card_json": json.dumps(body["card"])}
        return {"ok": True}


def test_build_card_full_structure():
    card = build_card(
        skills=["ocr-pro", {"id": "tts", "name": "语音合成", "tags": ["audio"]}],
        name="my-ocr", deployment={"region": "cn-east-2"},
        accepts=["peer_account", "direct_pay:alipay", "x402"],
        metering=["call_count", "output_tokens"],
        price={"ocr-pro": {"CNY": 3, "USDC": {"dimensions": [{"key": "call_count",
                                                              "amount": 5000000, "per": 1}]}}})
    assert card["name"] == "my-ocr"
    assert uuid.UUID(card["x-a2n"]["uid"])          # 合法 UUID
    assert card["accepts"] == ["peer_account", "direct_pay:alipay", "x402"]
    assert [s["id"] for s in card["skills"]] == ["ocr-pro", "tts"]
    assert card["skills"][1]["inputModes"] == ["application/json"]
    # 简写价展开为 v2 每次（call_count）单价；v2 完整写法原样透传
    pb = card["x-a2n"]["price_book"]["ocr-pro"]
    assert pb["CNY"] == {"dimensions": [{"key": "call_count", "amount": 3, "per": 1}]}
    assert pb["USDC"]["dimensions"][0]["amount"] == 5000000
    # 计量维度：可计费的才进账单
    dims = card["x-a2n"]["metering"]["dimensions"]
    assert [d["key"] for d in dims] == ["call_count", "output_tokens"]
    # 描述未填 → 自动生成且提到技能名与部署属地（不写硬件细节，能力是黑盒）
    assert "OCR 识别" in card["description"] or "ocr-pro" in card["description"]
    assert "cn-east-2" in card["description"]
    assert card["x-a2n"]["deployment"] == {"region": "cn-east-2"}


def test_auto_desc_matches_console_logic():
    d = auto_desc([{"id": "ocr-pro", "name": "OCR 识别"}], {"region": "cn-east-2"})
    assert d.startswith("提供OCR 识别（ocr-pro）服务 · 部署于 cn-east-2")
    assert "4090" not in d


def test_shelf_posts_registry_with_card():
    c = Recording()
    r = shelf(c, skills=["ocr-pro"], name="n1", price={"ocr-pro": {"CNY": 3}})
    m, path, body, _ = c.calls[-1]
    assert (m, path) == ("POST", "/v1/registry/agents")
    assert body["visibility"] == "public"
    card = body["card"]
    assert card["name"] == "n1" and card["x-a2n"]["uid"]
    assert r["agent_id"] == "ag_new" and r["uid"] == card["x-a2n"]["uid"]
    assert r["card"] is card


def test_from_card_backfills_uid_and_accepts_string():
    c = Recording()
    raw = json.dumps({"name": "x", "url": "http://a", "skills": [{"id": "s"}]})
    r = from_card(c, raw)
    _, _, body, _ = c.calls[-1]
    assert uuid.UUID(body["card"]["x-a2n"]["uid"])   # 缺 uid 自动补
    assert r["agent_id"] == "ag_new"


def test_update_card_put_path():
    c = Recording()
    update_card(c, "ag_1", {"name": "x", "url": "u", "skills": [{"id": "s"}],
                            "x-a2n": {"uid": "keep"}})
    m, path, body, _ = c.calls[-1]
    assert (m, path) == ("PUT", "/v1/registry/agents/ag_1/card")
    assert body == {"card": {"name": "x", "url": "u", "skills": [{"id": "s"}],
                             "x-a2n": {"uid": "keep"}}}


@pytest.fixture
def stub_platform():
    """本地 stub 平台：CLI 直调 main() 时避免真实出网。"""
    class H(BaseHTTPRequestHandler):
        def _reply(self, obj):
            data = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            self.rfile.read(n)
            self._reply({"agent_id": "ag_new", "card_hash": "h",
                         "status": "PROBATION", "kya_grade": "B"})

        def do_PUT(self):
            n = int(self.headers.get("Content-Length", 0))
            self.rfile.read(n)
            self._reply({"ok": True})

        def do_GET(self):
            self._reply([])

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_cli_shelf_key_fields(stub_platform, capsys):
    rc = main(["--platform", stub_platform, "--principal", "me", "shelf",
               "--skill", "ocr-pro", "--region", "cn-east-2",
               "--price", "CNY:call_count:3", "--accept", "peer_account",
               "--dim", "output_tokens", "--desc", "自写描述"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["agent_id"] == "ag_new"
    card = out["card"]
    assert card["x-a2n"]["deployment"] == {"region": "cn-east-2"}
    assert card["accepts"] == ["peer_account"]
    assert card["description"] == "自写描述"
    assert card["x-a2n"]["price_book"]["ocr-pro"]["CNY"] == {
        "dimensions": [{"key": "call_count", "amount": 3, "per": 1}]}


def test_cli_shelf_with_card_file(stub_platform, tmp_path, capsys):
    f = tmp_path / "card.json"
    f.write_text(json.dumps({"name": "file-card", "url": "http://a",
                             "skills": [{"id": "s"}]}), encoding="utf-8")
    rc = main(["--platform", stub_platform, "--principal", "me", "shelf",
               "--card", str(f)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["card"]["name"] == "file-card"
    assert uuid.UUID(out["uid"])


def test_cli_errors_go_stderr(capsys):
    rc = main(["--platform", "http://x", "--principal", "me", "shelf"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("error:") and "要么 --card" in err
