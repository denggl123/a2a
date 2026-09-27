"""跨网络核对与节点自检这两个脚本的守卫测试。

这两个脚本是"下一道门槛"（两台不同网络的电脑互相发现、免费调用、双方核验收据）
在**调用方那一半**的可复现入口；它们的行为退回一步，门槛就再也拿不到可作假的证据
之外的东西。所以这里钉住：

* 两个脚本至少能编译（语法关卡，拦住手滑）；
* 跨网络核对的纯逻辑：什么算"本机地址"、什么算"远端卡"（这是"跨网络"三个字
  能不能成立的判据——判错了就会把本机自己的供给当成远端的）；
* 节点自检的 CLI 形状（--home / --source 等）不丢。
"""
from __future__ import annotations

import importlib.util
import py_compile
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "scripts"


def _load(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_a2n_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_both_tools_compile():
    for name in ("cross_network_check", "node_doctor"):
        py_compile.compile(str(SCRIPTS / f"{name}.py"), doraise=True)


def test_cross_network_helpers_classify_local_vs_remote():
    m = _load("cross_network_check")

    # 回环一律算"本机"——含 docker 反代口 127.0.0.1:8791
    assert m.is_local_url("http://127.0.0.1:8791/a2a/svc_x")
    assert m.is_local_url("http://localhost:8890/a2a/svc_x")
    assert not m.is_local_url("https://node.example.com/a2a/svc_x")
    assert not m.is_local_url("http://172.245.148.171:8891/a2a/svc_x")

    assert m.host_of("https://A.B.C/a2a") == "a.b.c"       # 大小写归一
    assert m.host_of("") == ""


def test_cross_network_pick_remote_skips_local_and_filters_did():
    m = _load("cross_network_check")

    def card(url: str, did: str) -> dict:
        return {"card": {"url": url,
                         "x-a2n": {"sovereign": {"did": did}}}}

    results = [
        card("http://127.0.0.1:8791/a2a/svc_local", "did:a2n:ag_local"),
        card("https://remote.example/a2a/svc_r", "did:a2n:ag_remote1"),
        card("https://other.example/a2a/svc_r2", "did:a2n:ag_remote2"),
    ]

    # 默认挑第一条"远端"（跳过回环）
    assert m.pick_remote(results, None)["card"]["url"].endswith("svc_r")
    # did 前缀过滤：只认 ag_remote2
    got = m.pick_remote(results, "did:a2n:ag_remote2")
    assert got is not None and got["card"]["url"].endswith("svc_r2")
    # 全是本机时挑不出来
    assert m.pick_remote([results[0]], None) is None


def test_doctor_cli_keeps_its_switches():
    m = _load("node_doctor")
    # 直接复用脚本里的 main 构造不易，退一步：源码里这些开关必须在
    src = (SCRIPTS / "node_doctor.py").read_text(encoding="utf-8")
    for flag in ("--home", "--node-port", "--proxy-port", "--p2p-port", "--source"):
        assert f'"{flag}"' in src, f"doctor 少了开关 {flag}"
    assert hasattr(m, "check_proxy") and hasattr(m, "check_p2p_shape")
