"""消息表体检：缺键、死键、语言覆盖、值里混进 HTML / 键里混进汉字。

为什么要有这个脚本：消息表是**唯一**的文案来源（骨架里已经没有硬编码中文），
所以「代码引用了表里没有的键」在界面上就是一片空白或一个 `rt.xxx` 键名，
而「表里有、代码里没人用」是迁移留下的死文案。两者都只能靠机器扫出来。

用法：
    python scripts/i18n_check.py            # 体检；缺键/坏值退出 1
    python scripts/i18n_check.py --strict   # 死键也算失败
    python scripts/i18n_check.py --coverage # 只打各语言覆盖率

动态键（前缀查表）不参与「死键」判定，但参与「缺键」判定：
`I18N.text('rt.cat.'+id)` 这种拼出来的键，脚本从源码里收集所有
「以 `.` 结尾的字符串字面量 +」当前缀，例如 `'rt.cat.'`、`'state.'`。
新增一处前缀查表不用改本脚本。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "packages" / "a2n-sdk" / "src" / "a2n_sdk" / "web"
TABLE = WEB / "i18n.js"

# 语言块：`    'zh-CN': {` … `    },` / `    'en': {}`
LOCALE_OPEN = re.compile(r"^\s{4}'([A-Za-z]{2,3}(?:-[A-Za-z0-9]+)*)':\s*\{\s*$")
LOCALE_EMPTY = re.compile(r"^\s{4}'([A-Za-z]{2,3}(?:-[A-Za-z0-9]+)*)':\s*\{\s*\}\s*,?\s*$")
LOCALE_CLOSE = re.compile(r"^\s{4}\}\s*,?\s*$")
# 键值对：键必须 ASCII（键是协议语义，不是文案）
ENTRY = re.compile(r"^\s+'([A-Za-z][A-Za-z0-9_.-]*)':\s*'(.*)',?\s*$")
# 引用：I18N.t('x') / I18N.text('x')
CALL = re.compile(r"I18N\.(?:t|text)\(\s*'([A-Za-z][A-Za-z0-9_.-]*)'")
# 标记：data-i18n="x" / data-i18n-placeholder="x" …
MARKUP = re.compile(r'data-i18n(?:-[a-z-]+)?="([A-Za-z][A-Za-z0-9_.-]*)"')
# 动态前缀：'rt.cat.' + id（单双引号都算）
PREFIX = re.compile(r"""['"]([A-Za-z][A-Za-z0-9_.-]*\.)['"]\s*\+""")
# 任意字符串字面量：有些键是当**数据**传的（`[['quality','rt.fb.dim.quality']]` 再由
# `I18N.t(label)` 取词），不在调用点出现。键名在源码里出现过就算引用。
LITERAL = re.compile(r"""['"]([A-Za-z][A-Za-z0-9_.-]*)['"]""")
CJK = re.compile(r"[\u4e00-\u9fa5]")
HTMLISH = re.compile(r"[<>]")


def parse_tables(text: str) -> dict[str, dict[str, str]]:
    """从 i18n.js 里解析出 {locale: {key: value}}。

    只认单行字符串值 —— 表里的值本来就是单行的（换行用 \\n 转义）。
    多行值会解析失败并被下面的断言当成「表结构与预期不符」暴露出来。
    """
    tables: dict[str, dict[str, str]] = {}
    current: str | None = None
    for line in text.splitlines():
        empty = LOCALE_EMPTY.match(line)
        if empty:
            tables.setdefault(empty.group(1), {})
            current = None
            continue
        opened = LOCALE_OPEN.match(line)
        if opened:
            current = opened.group(1)
            tables.setdefault(current, {})
            continue
        if current and LOCALE_CLOSE.match(line):
            current = None
            continue
        if current:
            entry = ENTRY.match(line)
            if entry:
                key, value = entry.groups()
                # 反转义：表里只有 \' 与 \n 两种（值里没有别的转义）
                value = value.replace("\\'", "'").replace("\\n", "\n")
                tables[current][key] = value
    return tables


def scan_sources(keys: set[str]) -> tuple[set[str], set[str], list[str]]:
    """返回 (出现过的键, 动态前缀, 扫描的文件列表)。

    - 以 `.` 结尾的引用是前缀（`I18N.text('rt.cat.'+id)` 的左边），进前缀集合，
      不算「引用了一个叫 rt.cat. 的键」—— 否则每次都会误报缺键。
    - 键名作为普通字符串出现在源码里也算引用：有些键是当数据传的（维度表的第二列
      就是键名，再由 `I18N.t(label)` 取词），只扫调用点会误判成死键。
    """
    used: set[str] = set()
    prefixes: set[str] = set()
    # 排除消息表自己：表里每个键都是字面量，扫进来就等于「全都用上了」
    files = sorted(p for p in [*WEB.glob("*.js"), *WEB.glob("*.html")] if p != TABLE)
    for path in files:
        text = path.read_text(encoding="utf-8")
        for key in CALL.findall(text):
            (prefixes if key.endswith(".") else used).add(key)
        used.update(MARKUP.findall(text))
        prefixes.update(PREFIX.findall(text))
        used.update(k for k in LITERAL.findall(text) if k in keys)
    return used, prefixes, [str(p.relative_to(ROOT)) for p in files]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="消息表体检")
    ap.add_argument("--strict", action="store_true", help="死键也算失败")
    ap.add_argument("--coverage", action="store_true", help="只打覆盖率")
    args = ap.parse_args(argv)

    if not TABLE.exists():
        print(f"✗ 找不到消息表：{TABLE}")
        return 1
    tables = parse_tables(TABLE.read_text(encoding="utf-8"))
    if "zh-CN" not in tables or not tables["zh-CN"]:
        print("✗ 消息表里没有 zh-CN，或解析不出来（值必须写成单行 '键': '值',）")
        return 1

    base = tables["zh-CN"]
    used, prefixes, files = scan_sources(set(base))
    print(f"  消息表 {len(base)} 条（zh-CN），扫描 {len(files)} 个前端文件，"
          f"直接引用 {len(used)} 个键、动态前缀 {len(prefixes)} 个")

    problems: list[str] = []

    # ① 缺键：代码引用了表里没有的键 → 界面上会出现键名或空白
    missing = sorted(k for k in used if k not in base)
    if missing:
        problems.append(f"缺键 {len(missing)} 个（代码引用了表里没有的键）："
                        + "、".join(missing[:8]) + ("…" if len(missing) > 8 else ""))

    # ② 死键：表里有、没人引用，也不在任何动态前缀下
    def covered(key: str) -> bool:
        return key in used or any(key.startswith(p) for p in prefixes)

    dead = sorted(k for k in base if not covered(k))
    if dead:
        msg = (f"死键 {len(dead)} 个（表里有、代码里没人用）："
               + "、".join(dead[:8]) + ("…" if len(dead) > 8 else ""))
        if args.strict:
            problems.append(msg)
        else:
            print(f"  · {msg}")

    # ③ 值里不许有 HTML：标记留在代码里，否则换个语言就可能注入标签
    html_keys = sorted(k for k, v in base.items() if HTMLISH.search(v))
    if html_keys:
        problems.append(f"值里带尖括号 {len(html_keys)} 条（HTML 必须留在代码里）："
                        + "、".join(html_keys[:8]))

    # ④ 键不许有汉字：键是协议语义，加语言时不能动
    han_keys = sorted(k for k in base if CJK.search(k))
    if han_keys:
        problems.append(f"键里出现汉字 {len(han_keys)} 个：" + "、".join(han_keys[:8]))

    # ⑤ 其它语言必须是中文表的子集：拼错一个键，那一条永远不会生效，且没人发现
    for locale, table in tables.items():
        if locale == "zh-CN":
            continue
        extra = sorted(set(table) - set(base))
        if extra:
            problems.append(f"{locale} 有 {len(extra)} 个中文表里没有的键（拼错了？）："
                            + "、".join(extra[:8]))
        for key, value in table.items():
            if HTMLISH.search(value):
                problems.append(f"{locale} 的 {key} 值里带尖括号（HTML 必须留在代码里）")
            if not str(value).strip():
                problems.append(f"{locale} 的 {key} 是空串（要回退中文就删掉这条，别留空）")

    print("\n  各语言覆盖率：")
    for locale in tables:
        n = len(tables[locale])
        pct = (100.0 * n / len(base)) if base else 0.0
        print(f"    {locale:<6} {n:>5} / {len(base)}  ({pct:5.1f}%)")

    if args.coverage:
        return 0 if not problems else 1

    if problems:
        print()
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print("\n  ✓ 消息表自洽：无缺键、无坏值、其它语言是中文表的子集")
    return 0


if __name__ == "__main__":
    sys.exit(main())
