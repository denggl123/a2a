"""The installed SDK node owns its console; platform pairing is optional."""
from pathlib import Path


ROOT = Path(__file__).parents[1]
PLATFORM_WEB = ROOT / "packages" / "a2n-server" / "src" / "a2n_server" / "web"
NODE_CONSOLE = (ROOT / "packages" / "a2n-sdk" / "src" / "a2n_sdk" /
                "web" / "runtime.html")


def test_sdk_node_console_owns_the_complete_local_product_surface():
    console = NODE_CONSOLE.read_text(encoding="utf-8")

    assert '>找 Agent</button>' in console
    assert '>卖 Agent</button>' in console
    assert 'id="projections"' in console
    assert 'id="bindings"' in console
    assert 'id="accountsList"' in console
    assert 'id="p2pState"' in console
    assert '连接这台电脑的节点' not in console
    assert '本机打开当前页面不需要配对' in console


def test_platform_console_does_not_own_or_pair_the_local_node():
    console = (PLATFORM_WEB / "console.html").read_text(encoding="utf-8")

    assert 'A2NLocal.open()' not in console
    assert 'local-node.js' not in console
    assert not (PLATFORM_WEB / "local-node.js").exists()
