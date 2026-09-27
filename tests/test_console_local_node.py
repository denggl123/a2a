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


def test_find_page_offers_multifacet_discovery():
    """发现页是"多维度筛选"的浏览页，不是只有一个搜索框。"""
    console = NODE_CONSOLE.read_text(encoding="utf-8")

    assert 'id="facets"' in console                      # 左侧维度栏
    assert 'data-facet' in console                       # 维度可点
    assert 'SKILL_CATEGORY' in console                   # 行业分类映射
    assert 'CATEGORY_ORDER' in console
    assert '按已知能力清单浏览' in console               # 浏览入口
    assert 'id="discSort"' in console                    # 排序
    assert 'id="vwList"' in console                      # 紧凑视图（名称/描述各占一列）
    assert 'id="discQ"' in console                       # 在候选里搜
    assert 'data-clear' in console                       # 已选条件可摘除
    assert 'id="results"' in console
    assert 'id="searchErrors"' in console                # 取数失败必须说出来


def test_find_page_price_wording_stays_honest():
    """口径铁律：未标价不自动等于免费。筛选与文案都必须守住这条。"""
    console = NODE_CONSOLE.read_text(encoding="utf-8")

    assert 'PRICE_LABEL={free:' in console
    assert "unpriced:'未标价'" in console
    assert '未标价不自动等于免费' in console
    assert '按免费服务展示' not in console               # 曾与"未标价"口径自相矛盾
    assert '免费（已声明零价）' in console               # 免费 = 供给方明确声明零价


def test_find_page_browse_is_labelled_not_the_whole_network():
    """浏览 = 按已知能力清单逐条查。不许把它说成"全网目录"。"""
    console = NODE_CONSOLE.read_text(encoding="utf-8")

    assert 'BROWSE_SEED' in console
    assert '不是全网目录' in console
    assert 'browseSkills' in console
