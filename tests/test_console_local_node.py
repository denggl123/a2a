"""The platform console is the only product UI for the local runtime."""
from pathlib import Path


WEB = (Path(__file__).parents[1] / "packages" / "a2n-server" / "src"
       / "a2n_server" / "web")


def test_platform_console_owns_local_runtime_management_surface():
    console = (WEB / "console.html").read_text(encoding="utf-8")
    adapter = (WEB / "local-node.js").read_text(encoding="utf-8")

    assert '>本机 Agent</button>' in console
    assert 'data-account' in adapter
    assert 'data-supply' in adapter
    assert 'data-projections' in adapter
    assert 'data-network' in adapter
    assert '打开本机管理页' not in adapter
    assert "base + '/console'" not in adapter


def test_local_runtime_copy_explains_backend_not_second_console():
    adapter = (WEB / "local-node.js").read_text(encoding="utf-8")

    assert '仍在当前平台控制台完成配置' in adapter
    assert '它是后台组件，不需要作为另一套控制台使用' in adapter
    assert 'API 凭据只保存在这台电脑' in adapter
