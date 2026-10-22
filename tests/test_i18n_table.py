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
    _mod, tables = _tables()
    base = set(tables["zh-CN"])
    for locale, table in tables.items():
        if locale == "zh-CN":
            continue
        extra = sorted(set(table) - base)
        assert not extra, f"{locale} 有中文表里没有的键（拼错了就永远不生效）：" + "、".join(extra[:10])
        empty = [k for k, v in table.items() if not str(v).strip()]
        assert not empty, f"{locale} 有空串值（要回退中文就删掉这条）：{empty[:5]}"
