"""消息表契约：前端文案只有一个来源，表与代码必须对得上。

为什么要有这个测试：阶段 1 之后 `runtime.html` 的骨架里已经没有硬编码中文，
所有展示文字都来自 `web/i18n.js` 的 `MESSAGES`。于是两类退化都只能靠机器发现：

  1. **缺键** —— 代码引用 `I18N.t('rt.foo')` 但表里没有：界面上直接出现 `rt.foo`
     这种键名，或者一片空白。功能测试照样全过。
  2. **死键** —— 表里有、代码里没人用：多半是迁移时留下的旧文案。它不会坏，
     但会让「补英文」变成补一堆永远不会显示的字。

另外三条不变量：值里不许有 HTML（标记留在代码里）、键里不许有汉字（键是协议语义）、
其它语言的键必须是中文表的子集（拼错一个键，那一条永远不生效，而且没人发现）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "i18n_check.py"


def _module():
    spec = importlib.util.spec_from_file_location("i18n_check", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _tables():
    mod = _module()
    tables = mod.parse_tables(mod.TABLE.read_text(encoding="utf-8"))
    assert tables.get("zh-CN"), "消息表解析不出来：值必须写成单行 '键': '值',"
    return mod, tables


def test_every_referenced_key_exists():
    mod, tables = _tables()
    base = tables["zh-CN"]
    used, _prefixes, _files = mod.scan_sources(set(base))
    missing = sorted(k for k in used if k not in base)
    assert not missing, "这些键被引用了但表里没有（界面上会露出键名）：" + "、".join(missing)


def test_no_dead_keys():
    """表里每条都要有出处：调用点、标记、当数据传的字面量，或前缀查表。"""
    mod, tables = _tables()
    base = tables["zh-CN"]
    used, prefixes, _files = mod.scan_sources(set(base))
    dead = sorted(k for k in base if k not in used and not any(k.startswith(p) for p in prefixes))
    assert not dead, "这些键没人用（迁移残留？）：" + "、".join(dead[:20])


def test_values_carry_no_markup():
    _mod, tables = _tables()
    for locale, table in tables.items():
        bad = sorted(k for k, v in table.items() if "<" in v or ">" in v)
        assert not bad, f"{locale} 的这些值里有 HTML（标记必须留在代码里）：" + "、".join(bad)


def test_keys_are_ascii():
    _mod, tables = _tables()
    for locale, table in tables.items():
        bad = [k for k in table if not k.isascii()]
        assert not bad, f"{locale} 的键里有非 ASCII（键是协议语义，不是文案）：{bad[:5]}"


def test_other_locales_are_subsets_of_chinese():
    """`err.*` 例外：中文侧没有这些键，改由 test_error_translations_match_codes 对照登记表校验。"""
    _mod, tables = _tables()
    base = set(tables["zh-CN"])
    for locale, table in tables.items():
        if locale == "zh-CN":
            continue
        extra = sorted(k for k in set(table) - base if not k.startswith("err."))
        assert not extra, f"{locale} 有中文表里没有的键（拼错了就永远不生效）：" + "、".join(extra[:10])
        empty = [k for k, v in table.items() if not str(v).strip()]
        assert not empty, f"{locale} 有空串值（要回退中文就删掉这条）：{empty[:5]}"


def test_audit_catches_untranslated_and_placeholder_drift():
    """体检工具的「占位符 / 漏翻」两条检查必须真的会响。

    这两条只在某批译文写坏时才报警，正常状态下永远是绿的 —— 所以它们本身需要
    一次会红的测试，否则写错了也没人知道（比如正则写反、比例算错）。
    """
    mod = _module()
    base = {f"a.k{i}": f"第 {i} 项 {{count}}" for i in range(40)}
    base["a.plain"] = "开始使用这台节点"
    good = {k: f"Item {i}: {{count}}" for i, k in enumerate(k for k in base if k.startswith("a.k"))}
    good["a.plain"] = "Get started with this node"
    assert mod.value_problems("en", good, base) == [], "全英文、占位符一致时不该报警"

    missing = dict(good, **{"a.k0": "Item 0"})
    assert any("占位符" in p for p in mod.value_problems("en", missing, base)), "少了 {count} 要报"

    extra = dict(good, **{"a.k1": "Item 1: {count} {who}"})
    assert any("占位符" in p for p in mod.value_problems("en", extra, base)), "多了 {who} 要报"

    copied = dict(good, **{"a.plain": "开始使用这台节点"})
    assert any("没翻" in p for p in mod.value_problems("en", copied, base)), "整句照抄中文要报"

    half = dict(good, **{"a.plain": "Get started 开始使用这台节点"})
    assert any("没翻" in p for p in mod.value_problems("en", half, base)), "半句没翻也要报"

    # 本来就是汉字表的语言（将来的日语）不能被误报：这条检查按「汉字占比」判断，
    # 整张表都是汉字时它自动失效。
    ja = {k: f"第{i}項 {{count}}" for i, k in enumerate(k for k in base if k.startswith("a.k"))}
    ja["a.plain"] = "このノードを使い始める"
    assert mod.value_problems("ja", ja, base) == [], "汉字表不该被判成漏翻"


def test_error_translations_match_codes():
    """`err.<CODE>` 的码必须真在 docs/ERROR-CODES.md 里。

    这条替代了「子集」检查对 `err.*` 的保护：中文侧没有这些键（默认语言直接显示
    服务端原文），所以拼错一个码不会被子集规则抓到 —— 只会在用户真的遇到那个
    错误时，悄悄退回中文，谁也不会发现。
    """
    mod, tables = _tables()
    codes = set(mod.ERR_ROW.findall(mod.ERROR_CODES.read_text(encoding="utf-8")))
    assert codes, "错误码登记表解析不出来"
    for locale, table in tables.items():
        if locale == "zh-CN":
            continue
        bad = sorted(k[len("err."):] for k in table
                     if k.startswith("err.") and k[len("err."):] not in codes)
        assert not bad, f"{locale} 有登记表里没有的错误码（拼错了就永远不生效）：" + "、".join(bad[:10])
