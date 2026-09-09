"""SDK 封装正确性：不发真实请求，拦截 _req 检查路径与参数。

SDK 是给 agent / 程序用的门面。它自己不该有业务判断，
但"打到哪个端点、带什么参数"必须对——否则调用方会静默地调到错的地方。
"""
from a2n_sdk import Client


class Recording(Client):
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def _req(self, method, path, body=None, headers=None):
        self.calls.append((method, path, body, headers))
        return {"ok": True, "token": "T"}


def test_account_and_peer_wrappers():
    c = Recording()
    c.create_account("对公-研发线", "6222****0001")
    assert c.calls[-1][:3] == ("POST", "/v1/party-accounts",
                               {"label": "对公-研发线", "ref": "6222****0001"})

    c.accounts()
    assert c.calls[-1][:2] == ("GET", "/v1/party-accounts")

    c.propose_peer("pa_1", "ag_1", terms={"net_days": 15})
    m, p, b = c.calls[-1][:3]
    assert (m, p) == ("POST", "/v1/peers")
    assert b["account_id"] == "pa_1" and b["agent_id"] == "ag_1"
    assert b["terms"] == {"net_days": 15} and b["auto_accept"] is True

    c.peers("pa_1")
    assert c.calls[-1][1] == "/v1/peers?account_id=pa_1"
    c.peers()
    assert c.calls[-1][1] == "/v1/peers"

    c.accept_peer("pl_1")
    assert c.calls[-1][:2] == ("POST", "/v1/peers/pl_1/accept")
    c.peer_usable("pl_1")
    assert c.calls[-1][:2] == ("GET", "/v1/peers/pl_1/usable")


def test_deal_wrappers():
    c = Recording()
    c.open_deal("pl_1", "ocr-pro")
    assert c.calls[-1][:3] == ("POST", "/v1/deals",
                               {"link_id": "pl_1", "skill": "ocr-pro", "task_id": None})

    c.report_deal("dl_1", "provider", {"call_count": 10})
    m, p, b = c.calls[-1][:3]
    assert (m, p) == ("POST", "/v1/deals/dl_1/report")
    assert b["party"] == "provider" and b["dims"] == {"call_count": 10}

    c.reconcile_deal("dl_1")
    assert c.calls[-1][:2] == ("POST", "/v1/deals/dl_1/reconcile")

    c.issue_statement("pl_1", "2026-09")
    assert c.calls[-1][:3] == ("POST", "/v1/statements",
                               {"link_id": "pl_1", "period": "2026-09"})
    c.statements("pl_1")
    assert c.calls[-1][1] == "/v1/statements?link_id=pl_1"


def test_call_agent_takes_token_then_calls_relay():
    """调用门禁：先取凭据，再带 header 打中继——不能反过来，也不能漏。"""
    c = Recording()
    c.call_agent("ag_1", "invoke", {"q": 1})
    methods = [x[0] for x in c.calls]
    paths = [x[1] for x in c.calls]
    assert paths[0] == "/v1/transport/call-token"
    assert paths[1] == "/v1/relay/ag_1/invoke"
    assert c.calls[1][3] == {"X-A2N-Call": "T"}      # 凭据进了 header


def test_discover_passes_multidim_filter():
    c = Recording()
    c.discover("ocr-pro", filt={"accepts": ["peer_account"], "gpu": "A100"}, limit=5)
    m, p, b = c.calls[-1][:3]
    assert (m, p) == ("POST", "/v1/discovery/query")
    assert b["require"] == {"skill": "ocr-pro"}
    assert b["filter"] == {"accepts": ["peer_account"], "gpu": "A100"}
    assert b["limit"] == 5
