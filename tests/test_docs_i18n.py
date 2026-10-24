"""双语文档的完整性：英文版齐全、没残留中文、链接没断、跟得上中文版。

为什么要有这个测试：`scripts/docs_i18n_check.py` 是一把尺子，尺子自己得先被量过。
这些断言里有一条特别容易被人嫌烦 —— **同步戳过期就红**。它的意思是「中文版改过，
英文版还没复核」。删掉它很容易，删掉之后就没人知道英文版在说什么年代的事：
读者看到的是一份措辞严谨、日期具体、内容全错的文档，比没有英文版更糟。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


docs_check = _load("docs_i18n_check", ROOT / "scripts" / "docs_i18n_check.py")
error_codes = _load("error_codes", ROOT / "scripts" / "error_codes.py")


def _translated():
    return [pair for pair in docs_check.discover() if pair.has_en]


def test_scope_is_not_empty():
    """范围本身要能被发现，否则下面几条会在空集合上永远通过。"""
    pairs = docs_check.discover()
    assert len(pairs) > 20, f"只发现 {len(pairs)} 篇，范围可能被改坏了"
    assert _translated(), "一篇英文版都没有：要么没译，要么配对规则失效了"


def test_english_docs_pass_integrity():
    problems = []
    for pair in _translated():
        for problem in docs_check.integrity(pair):
            problems.append(f"{pair.rel}: {problem}")
    assert not problems, "英文文档体检不通过：\n  " + "\n  ".join(problems)


def test_generated_error_registry_is_in_sync():
    """英文登记表是生成的：源码改了没重生成，说明表格在描述旧世界。"""
    expected = error_codes.render_en(error_codes.scan())
    actual = (ROOT / "docs" / "ERROR-CODES.en.md").read_text(encoding="utf-8")
    assert actual == expected, (
        "docs/ERROR-CODES.en.md 与源码不一致，请运行 python scripts/error_codes.py --write")


def test_marker_detects_an_edited_source(tmp_path):
    """同步戳必须真的会过期 —— 否则它只是文件开头的一行装饰。"""
    zh = tmp_path / "SAMPLE.md"
    en = tmp_path / "SAMPLE.en.md"
    zh.write_text("# 标题\n\n正文。\n", encoding="utf-8")
    en.write_text(f"<!-- source: {docs_check.source_stamp(zh)} -->\n# Title\n\nBody.\n", encoding="utf-8")
    pair = docs_check.Pair(zh, en)
    assert docs_check.integrity(pair) == [], "刚写完的英文版不该报问题"

    zh.write_text("# 标题\n\n正文改了。\n", encoding="utf-8")
    problems = docs_check.integrity(pair)
    assert any(p.startswith("已过期") for p in problems), problems

    docs_check.stamp(pair)
    assert not any(p.startswith("已过期") for p in docs_check.integrity(pair))


def test_link_rule_points_english_readers_at_english_pages(tmp_path):
    """英文版里指向已译文档的链接必须写 `.en.md`，否则读者一点就掉回中文。"""
    (tmp_path / "A.md").write_text("# A\n", encoding="utf-8")
    (tmp_path / "A.en.md").write_text("# A\n", encoding="utf-8")
    zh = tmp_path / "B.md"
    zh.write_text("# B\n\n[A](A.md)\n", encoding="utf-8")
    en = tmp_path / "B.en.md"
    en.write_text(f"<!-- source: {docs_check.source_stamp(zh)} -->\n# B\n\n[A](A.md)\n", encoding="utf-8")
    problems = docs_check.integrity(docs_check.Pair(zh, en))
    assert any("该指向英文版" in p for p in problems), problems

    en.write_text(f"<!-- source: {docs_check.source_stamp(zh)} -->\n# B\n\n[A](A.en.md)\n", encoding="utf-8")
    assert docs_check.integrity(docs_check.Pair(zh, en)) == []


def test_structure_drift_is_reported(tmp_path):
    """漏译一整段时标题数会少一个 —— 这是最便宜的「翻译被截断」探测器。"""
    zh = tmp_path / "C.md"
    zh.write_text("# C\n\n## 一\n\n## 二\n", encoding="utf-8")
    en = tmp_path / "C.en.md"
    en.write_text(f"<!-- source: {docs_check.source_stamp(zh)} -->\n# C\n\n## One\n", encoding="utf-8")
    problems = docs_check.integrity(docs_check.Pair(zh, en))
    assert any("标题数对不上" in p for p in problems), problems


def test_fix_links_rewrites_navigation_but_not_examples(tmp_path):
    """链接改的是「哪一份文档」，不是「哪一段话」，所以可以机械替换 ——
    但代码块里的链接是示例，改了就把示例改坏了。"""
    (tmp_path / "A.md").write_text("# A\n", encoding="utf-8")
    (tmp_path / "A.en.md").write_text("# A\n", encoding="utf-8")
    zh = tmp_path / "B.md"
    zh.write_text("# B\n\n[A](A.md)\n\n```text\n[A](A.md)\n```\n", encoding="utf-8")
    en = tmp_path / "B.en.md"
    en.write_text(
        f"<!-- source: {docs_check.source_stamp(zh)} -->\n"
        "# B\n\n[A](A.md#part)\n\n```text\n[A](A.md)\n```\n",
        encoding="utf-8")
    pair = docs_check.Pair(zh, en)
    assert docs_check.fix_links(pair) == 1, "只该改导航那一处"
    text = en.read_text(encoding="utf-8")
    assert "[A](A.en.md#part)" in text, "锚点要保留"
    assert "```text\n[A](A.md)\n```" in text, "代码块里的示例不该动"
    assert docs_check.integrity(pair) == []


def test_han_in_prose_fails_but_quoted_han_is_allowed(tmp_path):
    """英文版里的汉字等于漏译；但反引号里的汉字是**引用**（术语表的中文列、断言里的
    中文原文），翻掉它读者就没法拿它去 grep 源码。这条钉子盯住两边 ——
    只放宽不放宽到底，放宽到散文里去就没人守着了。"""
    zh = tmp_path / "E.md"
    zh.write_text("# E\n\n| 术语 | 说明 |\n| --- | --- |\n| 节点 | node |\n", encoding="utf-8")
    marker = f"<!-- source: {docs_check.source_stamp(zh)} -->\n"
    en = tmp_path / "E.en.md"

    en.write_text(marker + "# E\n\n| Term | Note |\n| --- | --- |\n| `节点` | node |\n", encoding="utf-8")
    assert docs_check.integrity(docs_check.Pair(zh, en)) == [], "反引号里的中文是引用，不该报"

    en.write_text(marker + "# E\n\n| Term | Note |\n| --- | --- |\n| 节点 | node |\n", encoding="utf-8")
    problems = docs_check.integrity(docs_check.Pair(zh, en))
    assert any("汉字" in p for p in problems), problems


def test_dropped_table_row_is_reported(tmp_path):
    """19 行的条件表少一行，人眼几乎发现不了，读者却少一个选项。"""
    zh = tmp_path / "D.md"
    zh.write_text("# D\n\n| 条件 | 口径 |\n| --- | --- |\n| 一 | 甲 |\n| 二 | 乙 |\n", encoding="utf-8")
    en = tmp_path / "D.en.md"
    en.write_text(
        f"<!-- source: {docs_check.source_stamp(zh)} -->\n"
        "# D\n\n| Condition | Rule |\n| --- | --- |\n| one | alpha |\n",
        encoding="utf-8")
    problems = docs_check.integrity(docs_check.Pair(zh, en))
    assert any("表格行数对不上" in p for p in problems), problems
