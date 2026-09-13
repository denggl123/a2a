"""自持网络（a2n-node）测试：没有服务器、没有托管时，两个人也能互相发现调用。

分两段：

  · **单元**：卡片自证、双边互签、请求签名与反重放。纯函数，不碰库、不出网。
  · **集成**：真的起节点进程（每个进程一个库），走完"发现 → 取卡 → 直连调用 →
    双向互证"，以及"对方在干活之前先挡掉该挡的"。

为什么不在这一个进程里起两个节点：conftest 已经 import 了 a2n-store（全量套件
共用一个临时库），而"一个节点 = 一个进程 = 一个库"是硬约束。在同一进程里拼两个
节点，测的就不再是这套东西了 —— 集成段因此走**真进程**，顺带把 use_home 的
顺序纪律放到真实进程模型下验证。

本文件刻意不 import a2n_node.node / a2n_server / a2n_custodian：
自持模式的价值主张就是"这些东西不在场"，测试自己先做到。
"""
from __future__ import annotations

import copy
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from a2n_kernel.hashing import new_id, now_iso
from a2n_p2p import DID_PREFIX, Identity

from a2n_node import card as cardmod
from a2n_node import peer as peermod
from a2n_node import receipt as rcpt
from a2n_node.home import same_path, use_home

ROOT = Path(__file__).resolve().parents[1]
NODE_SCRIPT = ROOT / "scripts" / "sovereign_node.py"
PACKAGE_SRC = [str(p) for p in sorted((ROOT / "packages").glob("*/src"))]


def _opener():
    """本机回环必须绕开系统代理（被代理拦下的症状是 502，极难联想）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ============================ 单元：卡片自证 ============================

def _a_card(**kw):
    ident = Identity.generate()
    card = cardmod.build_card(ident, name=kw.pop("name", "alice"),
                              skills=kw.pop("skills", ["ocr-pro"]),
                              host="127.0.0.1", port=9661, p2p_port=9761, **kw)
    return ident, card


def test_did_is_the_fingerprint_of_the_public_key():
    """身份 = 公钥指纹。没有谁在分配 id，也就没有谁能在身份上卡人。"""
    ident = Identity.generate()
    assert cardmod.did_from_pub(ident.pub_raw) == ident.did
    assert ident.did.startswith(DID_PREFIX)


def test_card_proves_itself_without_any_authority():
    _, card = _a_card()
    ok, why = cardmod.verify_card(card, require_endpoint=True)
    assert ok, why
    assert cardmod.card_did(card) == cardmod.did_from_pub(
        cardmod.card_pub_raw(card))


def test_card_hash_is_the_registry_hash_not_a_second_one():
    """同一张卡在平台模式与自持模式下必须是同一个哈希，否则就是两套事实。"""
    from a2n_registry import card_hash as registry_hash

    _, card = _a_card()
    assert cardmod.card_hash(card) == registry_hash(card)


@pytest.mark.parametrize("field,new_value", [
    ("name", "被改过的名字"),
    ("url", "http://127.0.0.1:9999"),          # 形状合法，但仍然得改不动
    ("description", "被改过的描述"),
])
def test_card_rejects_a_changed_field(field, new_value):
    """签名域是整张卡：少签一个字段，那个字段就能被改而验签照样过。"""
    _, card = _a_card()
    bad = copy.deepcopy(card)
    bad[field] = new_value
    ok, why = cardmod.verify_card(bad)
    assert not ok and "签名" in why


def test_card_rejects_a_changed_capability():
    _, card = _a_card()
    bad = copy.deepcopy(card)
    bad["skills"][0]["id"] = "免费白嫖别人的名字"
    assert cardmod.verify_card(bad)[0] is False


def test_card_rejects_a_self_declared_identity():
    """用自己的钥匙签一张写着别人 did 的卡 —— 自证的第一道坎就在这。"""
    victim = Identity.generate()
    _, card = _a_card()
    bad = copy.deepcopy(card)
    bad["x-a2n"]["sovereign"]["did"] = victim.did
    ok, why = cardmod.verify_card(bad)
    assert not ok and "公钥指纹" in why


def test_card_rejects_another_keys_signature():
    """把签名换成别人签的：签名域是别人的卡，签不到我这张上。"""
    _, card = _a_card()
    other = Identity.generate()
    bad = copy.deepcopy(card)
    bad["x-a2n"]["sovereign"]["pub"] = cardmod.pub_b64(other.pub_raw)
    bad["x-a2n"]["sovereign"]["sig"] = other.sign(cardmod.card_body(card))
    ok, why = cardmod.verify_card(bad)
    assert not ok and "公钥指纹" in why


def test_card_without_a_direct_endpoint_is_not_callable():
    """没有中继可退：url 为空 = 这张卡在这个模式下不可用。"""
    ident = Identity.generate()
    card = cardmod.build_card(ident, name="a", skills=["echo"], port=0)
    assert cardmod.verify_card(card)[0] is True
    ok, why = cardmod.verify_card(card, require_endpoint=True)
    assert not ok and "直连地址" in why


def test_card_rejects_a_missing_self_proof_block():
    _, card = _a_card()
    bad = copy.deepcopy(card)
    del bad["x-a2n"]["sovereign"]
    ok, why = cardmod.verify_card(bad)
    assert not ok and "自证" in why


@pytest.mark.parametrize("field", ["did", "pub", "sig"])
def test_card_needs_all_three_parts_of_its_self_proof(field):
    """自证缺一块就不成立：只有 did 是自称，只有 pub 是没人认领的钥匙。"""
    _, card = _a_card()
    bad = copy.deepcopy(card)
    del bad["x-a2n"]["sovereign"][field]
    ok, why = cardmod.verify_card(bad)
    assert not ok and "自证" in why


# ============================ 单元：双边互签 ============================

def _pair_receipt(**over):
    prov, caller = Identity.generate(), Identity.generate()
    body = rcpt.make_body(task_id=new_id("t"), caller_did=caller.did,
                          provider_did=prov.did, skill="echo",
                          input_hash=rcpt.hash_payload({"text": "hi"}),
                          output_hash=rcpt.hash_payload({"text": "HI"}),
                          ts=now_iso())
    body.update(over)
    return prov, caller, rcpt.sign(prov, body)


def test_provider_receipt_verifies_on_the_callers_side():
    _, _, r = _pair_receipt()
    ok, why = rcpt.verify(r)
    assert ok, why
    assert rcpt.is_from(r, r["provider_did"])


@pytest.mark.parametrize("field", ["output_hash", "task_id", "input_hash", "skill"])
def test_receipt_rejects_a_changed_field(field):
    _, _, r = _pair_receipt()
    bad = dict(r)
    bad[field] = "改过了"
    ok, why = rcpt.verify(bad)
    assert not ok and "签名" in why


def test_receipt_rejects_a_swapped_signer():
    _, _, r = _pair_receipt()
    bad = dict(r)
    bad["by"] = Identity.generate().did          # 冒充另一个签名者
    ok, why = rcpt.verify(bad)
    assert not ok and "公钥指纹" in why


def test_receipt_rejects_a_third_party_signature():
    """签名者必须是这一单的两方之一 —— 否则任何路人都能替他们刻章。"""
    prov, caller, r = _pair_receipt()
    body = rcpt.body_of(r)
    onlooker = Identity.generate()
    ok, why = rcpt.verify(rcpt.sign(onlooker, body))
    assert not ok and "第三方" in why


def test_ack_points_at_exactly_one_receipt():
    _, caller, r = _pair_receipt()
    a = rcpt.ack(caller, r)
    ok, why = rcpt.verify_ack(a, r)
    assert ok, why
    assert a["of"] == r["sig"]                    # 引用，不是复述


def test_ack_does_not_transfer_to_another_receipt():
    _, caller, r = _pair_receipt()
    a = rcpt.ack(caller, r)
    _, _, other = _pair_receipt()
    ok, why = rcpt.verify_ack(a, other)
    assert not ok and "引用" in why


def test_ack_rejects_a_forged_signer():
    _, caller, r = _pair_receipt()
    a = rcpt.ack(caller, r)
    bad = dict(a)
    bad["by"] = Identity.generate().did
    assert rcpt.verify_ack(bad, r)[0] is False


def test_receipt_fingerprint_is_stable_and_content_sensitive():
    _, _, r = _pair_receipt()
    assert rcpt.fingerprint(r) == rcpt.fingerprint(copy.deepcopy(r))
    bad = dict(r)
    bad["output_hash"] = "0" * 64
    assert rcpt.fingerprint(bad) != rcpt.fingerprint(r)


# ============================ 单元：直连请求 ============================

def _req(provider_did: str = "", payload=None):
    """造一份已签名的调用请求。返回 (应答方的身份, 请求) —— 应答方身份用来签应答。"""
    provider = Identity.generate()
    caller = Identity.generate()
    req = peermod.sign_request(caller, provider_did=provider_did or provider.did,
                               skill="echo",
                               payload=payload if payload is not None else {"n": 1})
    return provider, req


def test_request_signature_and_payload_are_both_checked():
    _, req = _req()
    ok, why = peermod.verify_request(req)
    assert ok, why
    # 签名只覆盖载荷指纹 —— 改动载荷后签名仍然"有效"，必须靠指纹比对拦下
    bad = dict(req)
    bad["payload"] = {"n": 999}
    ok, why = peermod.verify_request(bad)
    assert not ok and "载荷" in why


def test_request_rejects_a_self_declared_caller():
    victim = Identity.generate()
    attacker = Identity.generate()
    req = peermod.sign_request(attacker, provider_did=Identity.generate().did,
                               skill="echo", payload={})
    bad = dict(req)
    bad["caller_did"] = victim.did
    assert peermod.verify_request(bad)[0] is False


def test_request_rejects_replay_and_stale_clocks():
    guard = peermod.ReplayGuard()
    _, req = _req()
    assert peermod.verify_request(req, guard=guard)[0] is True
    ok, why = peermod.verify_request(req, guard=guard)
    assert not ok and "nonce" in why           # 同一份请求原样重发
    _, fresh = _req()
    ok, why = peermod.verify_request(fresh, guard=guard, now=time.time() + 9999)
    assert not ok and "越窗" in why


def test_response_is_bound_to_the_request_it_answers():
    prov, req = _req()
    resp = peermod.sign_response(prov, req=req, state="accepted", result={"n": 2})
    assert peermod.verify_response(resp, req=req)[0] is True

    # 第一道闸：签名覆盖整个应答体，改动任何字段先在这里被拦下
    bad = dict(resp, msg_id="别的请求")
    ok, why = peermod.verify_response(bad, req=req)
    assert not ok and "签名" in why

    # 第二道闸：签名有效也不行 —— 答的必须是我这一份，否则是拿别人的应答顶包
    _, other_req = _req(provider_did=prov.did)
    resp2 = peermod.sign_response(prov, req=other_req, state="accepted", result={"n": 2})
    ok, why = peermod.verify_response(resp2, req=req)
    assert not ok and "请求" in why


def test_response_rejects_a_tampered_result():
    prov, req = _req()
    resp = peermod.sign_response(prov, req=req, state="accepted", result={"n": 2})
    bad = dict(resp)
    bad["result"] = {"n": 999999}
    assert peermod.verify_response(bad, req=req)[0] is False


# ============================ 单元：库的归属 ============================

def test_use_home_refuses_to_switch_after_the_store_is_loaded(tmp_path):
    """套件里 a2n-store 已经 import（库已锁死）—— 这时改库必须当场报错。"""
    assert "a2n_store" in sys.modules or "a2n_store.db" in sys.modules
    with pytest.raises(RuntimeError, match="一个节点 = 一个进程 = 一个库"):
        use_home(tmp_path / "另一个节点")


def test_use_home_accepts_an_inherited_env_before_the_store_is_loaded(tmp_path):
    """子进程继承来的 A2N_DB 是父进程的库，不该否决子进程自己的决定。

    这条不是在测一个边角：`--bootstrap` 起邻居节点就是靠它 ——
    父进程已经为自己的节点定过库，子进程一起来环境变量里就带着它。
    """
    inherited, mine = tmp_path / "父节点的库", tmp_path / "我的库"
    code = (
        "from a2n_node.home import use_home, db_path_of\n"
        f"h = use_home(r'{mine}')\n"
        "print(db_path_of(h))\n"
    )
    env = {**os.environ, "A2N_DB": str(inherited / "a2n.db"),
           "PYTHONPATH": os.pathsep.join(PACKAGE_SRC)}
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=env, cwd=str(ROOT), timeout=60)
    assert p.returncode == 0, p.stderr
    assert same_path(p.stdout.strip(), mine / "a2n.db")


# ============================ 集成：真进程跑真网络 ============================

def _free_port(kind: str = "tcp") -> int:
    fam = socket.SOCK_STREAM if kind == "tcp" else socket.SOCK_DGRAM
    with socket.socket(socket.AF_INET, fam) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _child_env() -> dict:
    env = {**os.environ}
    env.pop("A2N_DB", None)         # 子进程自己决定库（这正是"自持"的字面意思）
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost"
    env["PYTHONPATH"] = os.pathsep.join([*PACKAGE_SRC, env.get("PYTHONPATH", "")]).strip(os.pathsep)
    return env


def _spawn(name: str, home: Path, skill: str, *, extra: list[str] | None = None):
    http, p2p = _free_port(), _free_port("udp")
    cmd = [sys.executable, str(NODE_SCRIPT), "--name", name, "--home", str(home),
           "--skill", skill, "--http-port", str(http), "--p2p-port", str(p2p),
           "--json", *(extra or [])]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         cwd=str(ROOT), text=True, encoding="utf-8",
                         errors="replace", env=_child_env())
    return p, http, p2p


def _startup(p, timeout: float = 30.0) -> dict:
    """读节点启动时打印的那一行 JSON（它起来之后才会打印）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = p.stdout.readline()
        if not line:
            time.sleep(0.1)
            if p.poll() is not None:
                raise AssertionError(f"节点进程提前退出：{p.returncode}")
            continue
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError("等不到节点的启动信息")


def _wait_health(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with _opener().open(f"http://127.0.0.1:{port}/a2n/health", timeout=1.0) as f:
                if f.status == 200:
                    return True
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
            time.sleep(0.2)
    return False


def _json_lines(text: str) -> list[dict]:
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if ln.startswith("{"):
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
    return out


def _close(p) -> None:
    try:
        p.terminate()
        p.wait(timeout=10)
    except Exception:  # noqa: BLE001
        pass


def test_two_nodes_discover_and_call_each_other_across_processes(tmp_path):
    """**两个进程、两个库、一个种子地址** —— 发现、取卡、直连调用、双向互证。

    这是"只要有两个人用就能互相发现调用"的最小完整证明：调用方从没听过对方的
    地址，它只知道一个邻居；对方也不需要知道它。
    """
    prov_home, caller_home = tmp_path / "prov", tmp_path / "caller"
    prov, prov_http, prov_p2p = _spawn("prov", prov_home, "echo")
    try:
        prov_info = _startup(prov)
        assert _wait_health(prov_http), "供给方入口没起来"

        caller, caller_http, _ = _spawn("caller", caller_home, "echo", extra=[
            "--bootstrap", f"127.0.0.1:{prov_p2p}",
            "--call", "echo", "--call-payload", '{"text":"hello"}', "--wait", "3"])
        try:
            out, _ = caller.communicate(timeout=90)
        finally:
            _close(caller)

        docs = _json_lines(out)
        assert docs, f"调用方没有输出结论：\n{out}"
        doc = docs[-1]
        oc = doc["outcome"]
        assert oc, f"没有拿到调用结论：{doc}"
        assert oc["ok"] is True, f"调用失败：{oc.get('error')} / {doc.get('card_error')}"

        # ① 发现：只给一个种子，就找到了网络里的另一个人
        assert doc["offers"] and doc["offers"][0]["did"] == prov_info["did"]
        # ② 卡片自证：卡是对方签的，且与它在 gossip 里播的哈希一致
        assert doc["card"]["did"] == prov_info["did"]
        # ③ 交付内容真的是对方执行体产的
        assert oc["result"]["echo"] == {"text": "hello"}
        # ④ 凭据：收据由对方签，在**另一个进程**里照样验得过
        r = oc["receipt"]
        assert r and r["by"] == prov_info["did"]
        assert oc["peer_did"] == prov_info["did"]
        # ⑤ 调用方本地落了链（它自己的库，不是别人的）
        assert "完整" in doc["chain"]
        assert (caller_home / "a2n.db").exists()
        assert (prov_home / "a2n.db").exists()
        assert not same_path(caller_home / "a2n.db", prov_home / "a2n.db")

        # ⑥ 互证闭环的另一半：供给方那边记下了调用方的回执
        remote = peermod.fetch_chain(prov_info["endpoint"], limit=20)
        assert remote and remote["chain"].startswith("完整")
        assert any(x["event_type"] == "delivery.acknowledged"
                   for x in remote["items"]), remote
    finally:
        _close(prov)


def test_a_node_refuses_before_it_does_any_business(tmp_path):
    """没有中心门禁，所以**每个节点自己就是门禁**：先验身份，再谈业务。

    这一条同时说明"没有服务器"不等于"没有纪律"——纪律从平台搬到了每个节点里。
    """
    home = tmp_path / "closed"
    # 这个节点只做 echo，且把 echo 设为不对外开放（白名单为空 = 谁都进不来）
    prov, prov_http, _ = _spawn("closed", home, "echo",
                                extra=["--restricted", "echo"])
    try:
        info = _startup(prov)
        assert _wait_health(prov_http)

        # ① 卡片照样公开可取，并且本地就能验签（不需要问任何机构）
        req = urllib.request.Request(f"http://127.0.0.1:{prov_http}/.well-known/agent.json")
        with _opener().open(req, timeout=5) as f:
            card = json.loads(f.read().decode())
        ok, why = cardmod.verify_card(card, require_endpoint=True)
        assert ok, why
        assert cardmod.card_did(card) == info["did"]

        def post(pathobj):
            body = json.dumps(pathobj, ensure_ascii=False).encode()
            r = urllib.request.Request(f"http://127.0.0.1:{prov_http}/a2n/call",
                                       data=body, method="POST",
                                       headers={"Content-Type": "application/json"})
            try:
                with _opener().open(r, timeout=5) as f:
                    return f.status, json.loads(f.read().decode())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read().decode())

        # ② 没签名/乱签的一律 401：身份不过关，业务一个字都不谈
        code, out = post({"request": {"skill": "echo"}})
        assert code == 401 and out["stage"] == "identity", out

        # ③ 签名有效但不是我提供的能力 → 403（capability）
        stranger = Identity.generate()
        unknown = peermod.sign_request(stranger, provider_did=info["did"],
                                       skill="我不做的能力", payload={})
        code, out = post({"request": unknown})
        assert code == 403 and out["stage"] == "capability", out

        # ④ 签名有效、能力也有，但准入策略不放行 → 403（policy）
        caller = Identity.generate()
        good = peermod.sign_request(caller, provider_did=info["did"], skill="echo",
                                    payload={"text": "hi"})
        code, out = post({"request": good})
        assert code == 403 and out["stage"] == "policy", out
        assert "未对外开放" in out["error"]
    finally:
        _close(prov)
