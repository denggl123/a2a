"""消息表体检：缺键、死键、语言覆盖、坏值（HTML / 空串 / 漏翻 / 占位符对不上）。

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
# 错误码登记表（由 scripts/error_codes.py 生成）：`err.<CODE>` 的权威名单
ERROR_CODES = ROOT / "docs" / "ERROR-CODES.md"
ERR_ROW = re.compile(r"^\| `([A-Z][A-Z0-9_]{2,63})` \| .+? \| \d+ \|$", re.M)
# `err.*` 是**另一个命名空间**：中文侧就是服务端原文（errorText 在默认语言下原样返回），
# 不需要抄一份进 MESSAGES['zh-CN']。所以它不受「其它语言必须是中文表子集」约束，
# 而是改为对照 docs/ERROR-CODES.md 校验 —— 拼错一个码同样会被抓出来。
ERR_PREFIX = "err."

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
# 插值占位符：`{count}` / `{name}`。少了就少一个数字，多了就原样显示 `{foo}`。
PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]+)\}")
# 「这条语言基本不用汉字」的判据：汉字出现在极少数值里时，那几个值就是漏翻。
# 用比例而不是硬编码语言名 —— 将来加日语（大部分值本来就带汉字）这条会自动失效，
# 不会把正常的日语译文误报成漏翻。
HAN_CONTAMINATION_SHARE = 0.05


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


def value_problems(locale: str, table: dict[str, str], base: dict[str, str]) -> list[str]:
    """单张表的坏值检查：HTML、空串、占位符对不上、漏翻。返回问题描述。

    抽成函数是为了能被测试直接喂合成表 —— 一条永远不触发的检查等于没有检查，
    而这些检查只在「某批译文写坏了」时才该报警，正常状态下永远不会红。
    """
    out: list[str] = []
    for key, value in table.items():
        if HTMLISH.search(value):
            out.append(f"{locale} 的 {key} 值里带尖括号（HTML 必须留在代码里）")
        if not str(value).strip():
            out.append(f"{locale} 的 {key} 是空串（要回退中文就删掉这条，别留空）")
        # 占位符必须和中文一一对应：少一个 `{count}` 就是界面上少一个数字，
        # 多一个则是 `I18N.t()` 插不进去、界面上原样显示 `{foo}`。
        # `err.*` 的中文侧是服务端原文、不在这张表里，跳过。
        if key in base:
            want = set(PLACEHOLDER.findall(base[key]))
            got = set(PLACEHOLDER.findall(value))
            if want != got:
                out.append(f"{locale} 的 {key} 占位符对不上：中文 {sorted(want)}，"
                           f"{locale} {sorted(got)}")
    # 漏翻：这条语言整体几乎不用汉字，却有个别值带着汉字（复制了中文没改，
    # 或只翻了半句如 "Search 搜索"）。按比例判断，见 HAN_CONTAMINATION_SHARE。
    with_han = [k for k, v in table.items() if CJK.search(v)]
    if with_han and len(with_han) / len(table) < HAN_CONTAMINATION_SHARE:
        out.append(f"{locale} 有 {len(with_han)} 条值里带汉字（这条其实没翻）："
                   + "、".join(with_han[:8]))
    return out


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

    # ⑤ 其它语言必须是中文表的子集：拼错一个键，那一条永远不会生效，且没人发现。
    #    `err.*` 例外 —— 中文侧没有这些键（默认语言直接用服务端原文），
    #    所以改对照错误码登记表校验码名是否存在。
    codes = set(ERR_ROW.findall(ERROR_CODES.read_text(encoding="utf-8"))) if ERROR_CODES.exists() else set()
    err_translated: dict[str, int] = {}
    for locale, table in tables.items():
        if locale == "zh-CN":
            continue
        keys = set(table)
        err_keys = {k for k in keys if k.startswith(ERR_PREFIX)}
        err_translated[locale] = len(err_keys)
        keys -= err_keys
        extra = sorted(keys - set(base))
        if extra:
            problems.append(f"{locale} 有 {len(extra)} 个中文表里没有的键（拼错了？）："
                            + "、".join(extra[:8]))
        if codes:
            unknown = sorted(k[len(ERR_PREFIX):] for k in err_keys
                             if k[len(ERR_PREFIX):] not in codes)
            if unknown:
                problems.append(f"{locale} 有 {len(unknown)} 个登记表里没有的错误码（拼错了？）："
                                + "、".join(unknown[:8]))
        problems.extend(value_problems(locale, table, base))

    print("\n  界面文案覆盖率：")
    for locale in tables:
        n = len([k for k in tables[locale] if not k.startswith(ERR_PREFIX)])
        pct = (100.0 * n / len(base)) if base else 0.0
        print(f"    {locale:<6} {n:>5} / {len(base)}  ({pct:5.1f}%)")
    if codes:
        print(f"\n  错误说明（err.*，权威名单 {len(codes)} 个码）：")
        for locale, n in err_translated.items():
            pct = 100.0 * n / len(codes)
            print(f"    {locale:<6} {n:>5} / {len(codes)}  ({pct:5.1f}%)")

    if args.coverage:
        return 0 if not problems else 1

    if problems:
        print()
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print("\n  ✓ 消息表自洽：无缺键、无坏值、无漏翻、占位符与中文一致、其它语言是中文表的子集")
    return 0


if __name__ == "__main__":
    sys.exit(main())
