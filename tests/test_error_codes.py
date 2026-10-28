"""错误码契约：每条面向用户的中文 `raise` 必须带机器可读的码。

为什么要有这个测试：错误码是跨节点、跨语言的契约（`docs/I18N.md` 铁律 1、3）。
一旦有人新写一句 `raise ValueError("这个不行")`，前端就再也无法按码取译，
只能把中文原样甩给英文用户 —— 而这种退化在功能测试里完全看不出来。

三件事：
  1. 没有「中文 raise 不带码」；
  2. 码的形状合法（大写蛇形、不含汉字）；
  3. `docs/ERROR-CODES.md` 与源码同步（防止登记表变成过期文档）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "error_codes.py"


def _module():
    spec = importlib.util.spec_from_file_location("error_codes", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # dataclass 会按 cls.__module__ 回查 sys.modules，必须先登记再执行
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_every_chinese_raise_carries_a_code():
    mod = _module()
    missing = mod.uncoded(mod.scan())
    assert not missing, "这些中文 raise 没带错误码：" + "；".join(
        f"{s.where} {s.message[:40]}" for s in missing
    )


def test_codes_are_ascii_upper_snake():
    mod = _module()
    sites = mod.scan()
    assert sites, "扫描结果为空，说明扫描口径坏了"
    for site in sites:
        assert site.code, f"{site.where} 没带码"
        assert site.code == site.code.upper(), f"{site.where} 的码不是大写：{site.code}"
        assert site.code.replace("_", "").isalnum(), f"{site.where} 的码形状不对：{site.code}"
        assert site.code.isascii(), f"{site.where} 的码含非 ASCII 字符：{site.code}"


def test_registry_doc_is_current():
    mod = _module()
    expected = mod.render(mod.scan())
    actual = (ROOT / "docs" / "ERROR-CODES.md").read_text(encoding="utf-8")
    assert actual == expected, (
        "docs/ERROR-CODES.md 与源码不一致，请运行 python scripts/error_codes.py --write"
    )


def test_registry_covers_the_three_layers():
    """错误码要覆盖真正面向用户的三个入口：本地 API、管理面、节点服务。"""
    mod = _module()
    codes = mod.registry(mod.scan())
    for prefix in ("API_", "MANAGEMENT_", "PRODUCT_", "RESTORE_", "BACKUP_"):
        assert any(code.startswith(prefix) for code in codes), f"缺少 {prefix}* 一族错误码"


def test_concatenated_chinese_raise_is_not_invisible():
    """字符串拼接写出来的中文 raise 也要被扫到，不能从体检里隐身。

    为什么要有这条：`_text_of()` 早先只认字面量与 f-string，于是
    `raise ValueError("商品卡验签失败：" + str(reason))` 这种写法
    **既不算「已带码」也不算「缺码」** —— 它在登记表和体检里都看不见。

    真发生过：全仓唯一一处无码的中文 raise 就是这么写的，绕过了整套错误码约定，
    直到人工逐条复核才发现。

    这条测试自己也要会响：扫描器若退回只认字面量，它就得变红。
    """
    mod = _module()
    import ast

    tree = ast.parse(
        'raise ValueError("商品卡验签失败：" + str(reason))',
        mode="exec",
    )
    node = tree.body[0]
    text = mod._text_of(node.exc.args[0])
    assert text is not None, "拼接写法的中文 raise 扫不出来（它会从体检里隐身）"
    assert "商品卡验签失败" in text, f"扫出来的文案不对：{text!r}"

    # 带码的拼接同样要能认出来，否则它进不了登记表。
    tree2 = ast.parse(
        'raise ValueError("COORD_CARD_VERIFY_FAILED: 商品卡验签失败：" + str(reason))',
        mode="exec",
    )
    text2 = mod._text_of(tree2.body[0].exc.args[0])
    assert text2 is not None, "带码的拼接写法扫不出来"
    assert mod.CODE.match(text2), f"带码的拼接没认出码：{text2!r}"
