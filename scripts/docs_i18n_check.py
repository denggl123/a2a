"""文档双语体检：英文版是否齐全、是否同步、链接是否还指得对。

为什么要有这个脚本：界面文案和命令行文案都有表，改错了跑测试就红；文档没有。
一篇 3000 字的中文文档翻成英文后，中文继续改，英文没人会回头读 —— 半年后
英文版开始说「当前版本 0.2.0b3」，而仓库是 0.2.0b7，读者没有任何线索知道
自己读到的是化石。这里把「翻译有没有过期」变成机器能回答的问题。

配对规则只有一条，全仓库统一：

    X.md  ↔  X.en.md          （同目录、同名、`.en.md` 后缀）

选后缀而不是 `docs/en/` 镜像树，是为了**链接不用改写**：`../VERSION`、
`../scripts/README.md`、`../../docker/README.md` 在英文版里原样有效。镜像树
会让每个文档的每个相对链接都多退一层，61 篇里错一个就是死链，而链接错了
只有人去点才会发现。

链接口径因此可以机械化：英文版里指向 `foo.md` 的链接，如果 `foo.en.md` 存在，
就必须写成 `foo.en.md` —— 否则英文读者点一下就掉回中文。

  python scripts/docs_i18n_check.py             体检 + 覆盖率
  python scripts/docs_i18n_check.py --strict    过期翻译也算失败（发布前跑）
  python scripts/docs_i18n_check.py --stamp     复核后刷新同步戳
  python scripts/docs_i18n_check.py --list      列出每一对的状态

同步戳是英文版第一行的 `<!-- source: <中文版 sha256 前 12 位> -->`。中文版
一改，戳就对不上，脚本报「已过期」。
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 扫描范围：`docs/` 顶层 + 仓库根。`docs/design/` 是有中文文件名的历史设计稿
#: （`A2N-顶层架构总纲-v2.md`），翻译不在计划内；它们的中文文件名本身就不适合
#: 英文读者，真要翻得先改名，属于另一件事。
SCOPE_DIRS = (ROOT / "docs", ROOT)
SKIP_DIRS = {ROOT / "docs" / "design"}

SUFFIX = ".en.md"
CJK = re.compile(r"[\u4e00-\u9fa5]")
MARKER = re.compile(r"^<!--\s*source:\s*([0-9a-f]{12})\s*-->\s*$")
INLINE_CODE = re.compile(r"`[^`\n]*`")
LINK = re.compile(r"\]\(\s*(<[^>]*>|[^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
REF_LINK = re.compile(r"^\[[^\]]+\]:\s*(\S+)", re.M)
HEADING = re.compile(r"^#{1,6}\s", re.M)
FENCE = re.compile(r"^\s*```", re.M)


@dataclass
class Pair:
    """一篇中文文档与它的英文版。"""

    zh: Path
    en: Path

    @property
    def rel(self) -> str:
        return str(self.zh.relative_to(ROOT)).replace("\\", "/")

    @property
    def has_en(self) -> bool:
        return self.en.is_file()


def source_stamp(path: Path) -> str:
    """中文版的内容指纹。用归一化换行后的字节，避免 CRLF 工作区误报过期。"""
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()[:12]


def discover() -> list[Pair]:
    """列出范围内所有中文文档（`*.en.md` 不是中文文档，跳过）。"""
    seen: dict[Path, Pair] = {}
    for base in SCOPE_DIRS:
        for path in sorted(base.glob("*.md")):
            if path.name.endswith(SUFFIX) or path.parent in SKIP_DIRS:
                continue
            en = path.with_name(path.name[: -len(".md")] + SUFFIX)
            seen[path] = Pair(path, en)
    return [seen[key] for key in sorted(seen)]


def _strip_code(text: str) -> str:
    """去掉围栏代码块：里面的 `#` 是注释不是标题，`](` 也不是链接。"""
    out, fenced = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            out.append(line)
    return "\n".join(out)


def _targets(text: str) -> list[str]:
    body = _strip_code(text)
    found = [m.group(1) for m in LINK.finditer(body)]
    found += [m.group(1) for m in REF_LINK.finditer(body)]
    cleaned = []
    for raw in found:
        target = raw.strip("<>").strip()
        if not target or target.startswith("#"):
            continue
        if "://" in target or target.startswith("mailto:"):
            continue
        cleaned.append(target.split("#", 1)[0])
    return cleaned


def integrity(pair: Pair) -> list[str]:
    """英文版自身的问题：残留中文、死链、该指向英文版却指向中文版、结构对不上。"""
    problems: list[str] = []
    en_text = pair.en.read_text(encoding="utf-8")
    zh_text = pair.zh.read_text(encoding="utf-8")

    # ① 英文版里不该有汉字。真需要引中文（比如界面截图里的按钮），那是设计问题：
    #    界面上那一处也该有英文。
    #
    #    例外是**引用**：反引号里的汉字不算漏译。术语表的中文列（`结算` → settlement）、
    #    断言里那句中文原文（`` `过半覆盖之后…` ``）都必须照抄中文，翻掉它反而错 ——
    #    读者要拿它去 `grep` 源码。用行内代码而不是白名单，是因为这个区分对任何一篇都成立，
    #    不必为一篇文档开例外。
    han = [f"L{i}" for i, line in enumerate(en_text.splitlines(), 1)
           if CJK.search(INLINE_CODE.sub("", line))]
    if han:
        problems.append(f"英文版里还有汉字（{len(han)} 行，首行 {han[0]}）")

    # ② 同步戳：第一行必须存在且与中文版当前内容一致。
    first = en_text.splitlines()[0] if en_text.splitlines() else ""
    match = MARKER.match(first)
    if not match:
        problems.append("第一行缺少 `<!-- source: <12 位十六进制> -->` 同步戳")
    elif match.group(1) != source_stamp(pair.zh):
        problems.append("已过期：中文版改过，英文版没跟上（复核后跑 --stamp）")

    # ③ 链接：先判能不能解析，再判该不该指向英文版。
    for target in _targets(en_text):
        resolved = (pair.en.parent / target).resolve()
        if not resolved.exists():
            problems.append(f"死链：{target}")
            continue
        if target.endswith(".md") and not target.endswith(SUFFIX):
            sibling = (pair.en.parent / target).with_name(Path(target).stem + SUFFIX)
            if sibling.is_file():
                problems.append(f"该指向英文版：{target} → {sibling.name}")

    # ④ 结构：标题数与代码围栏数必须和中文版一致。少一个标题通常意味着
    #    某一段被整段漏译；围栏数不一致意味着代码块没闭合，后面全被吞进代码。
    zh_head, en_head = len(HEADING.findall(_strip_code(zh_text))), len(HEADING.findall(_strip_code(en_text)))
    if zh_head != en_head:
        problems.append(f"标题数对不上：中文 {zh_head}，英文 {en_head}")
    zh_fence, en_fence = len(FENCE.findall(zh_text)), len(FENCE.findall(en_text))
    if zh_fence != en_fence:
        problems.append(f"代码围栏数对不上：中文 {zh_fence}，英文 {en_fence}")

    # ⑤ 表格行数：一张 19 行的条件表少一行，人眼几乎发现不了，读者却少一个选项。
    zh_rows, en_rows = _table_rows(zh_text), _table_rows(en_text)
    if zh_rows != en_rows:
        problems.append(f"表格行数对不上：中文 {zh_rows}，英文 {en_rows}（漏了某一行？）")
    return problems


def _table_rows(text: str) -> int:
    """数表格正文行：以 `|` 开头、但不是 `|---|` 分隔行。"""
    return sum(1 for line in _strip_code(text).splitlines()
               if line.lstrip().startswith("|") and not re.fullmatch(r"[\s|:-]+", line))


def stamp(pair: Pair) -> None:
    """复核过之后刷新同步戳（只改第一行，不碰正文）。"""
    text = pair.en.read_text(encoding="utf-8")
    lines = text.splitlines()
    marker = f"<!-- source: {source_stamp(pair.zh)} -->"
    if lines and MARKER.match(lines[0]):
        lines[0] = marker
    else:
        lines.insert(0, marker)
    pair.en.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _english_target(target: str, base: Path) -> str | None:
    """把指向中文版的链接改成指向英文版；不需要改就返回 None。"""
    if "://" in target or target.startswith("mailto:"):
        return None
    path, _, anchor = target.partition("#")
    if not path.endswith(".md") or path.endswith(SUFFIX):
        return None
    sibling = (base / path).with_name(Path(path).stem + SUFFIX)
    if not sibling.is_file():
        return None
    new = Path(path).with_name(Path(path).stem + SUFFIX).as_posix()
    return new + (f"#{anchor}" if anchor else "")


def fix_links(pair: Pair) -> int:
    """改写英文版里的链接，让它们落在英文版上。

    人写的时候总会照着中文版抄链接；与其事后一条条找，不如让脚本改 ——
    改的是「哪一份文档」，不是「哪一段话」，所以机械替换是安全的。
    围栏代码块里的链接不动：那里是示例，不是导航。
    """
    lines = pair.en.read_text(encoding="utf-8").splitlines()
    fenced, changed, out = False, 0, []
    for line in lines:
        if FENCE.match(line):
            fenced = not fenced
            out.append(line)
            continue
        if fenced:
            out.append(line)
            continue
        edits = []
        for match in LINK.finditer(line):
            raw = match.group(1)
            target = raw.strip("<>").strip()
            new = _english_target(target, pair.en.parent)
            if new:
                edits.append((match.start(1), match.end(1), new))
        for start, end, new in reversed(edits):
            line = line[:start] + new + line[end:]
        changed += len(edits)
        out.append(line)
    if changed:
        pair.en.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="文档双语体检")
    parser.add_argument("--strict", action="store_true", help="过期翻译也算失败（发布前用）")
    parser.add_argument("--stamp", action="store_true", help="刷新全部同步戳")
    parser.add_argument("--fix-links", action="store_true", help="把该指向英文版的链接改过去")
    parser.add_argument("--list", action="store_true", help="逐篇打印状态")
    args = parser.parse_args(argv)

    pairs = discover()
    translated = [p for p in pairs if p.has_en]
    todo = [p for p in pairs if not p.has_en]

    if args.fix_links:
        total = sum(fix_links(pair) for pair in translated)
        print(f"已改写 {total} 个链接")
        return 0

    if args.stamp:
        for pair in translated:
            stamp(pair)
        print(f"已刷新 {len(translated)} 个同步戳")
        return 0

    failures: list[str] = []
    stale: list[str] = []
    for pair in translated:
        problems = integrity(pair)
        for problem in problems:
            if problem.startswith("已过期"):
                stale.append(f"{pair.rel}")
            failures.append(f"{pair.rel}: {problem}")
        if args.list:
            state = "过期" if any(p.startswith("已过期") for p in problems) else ("有问题" if problems else "ok")
            print(f"  {state:<4} {pair.rel}")

    total_han = sum(len(CJK.findall(p.zh.read_text(encoding="utf-8"))) for p in pairs)
    done_han = sum(len(CJK.findall(p.zh.read_text(encoding="utf-8"))) for p in translated)
    share = (done_han / total_han * 100) if total_han else 100.0

    print(f"\n{len(pairs)} 篇中文文档，已译 {len(translated)} 篇，待译 {len(todo)} 篇")
    print(f"按体量算：{done_han:,} / {total_han:,} 汉字（{share:.1f}%）")
    if todo and args.list:
        print("\n待译：")
        for pair in sorted(todo, key=lambda p: -len(CJK.findall(p.zh.read_text(encoding="utf-8")))):
            count = len(CJK.findall(pair.zh.read_text(encoding="utf-8")))
            print(f"  {count:>6} 汉字  {pair.rel}")

    if failures:
        print(f"\n发现 {len(failures)} 个问题：", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
    blocking = [f for f in failures if not f.split(": ", 1)[-1].startswith("已过期")]
    if blocking or (args.strict and stale):
        return 1
    if stale:
        print(f"\n{len(stale)} 篇翻译已过期（加 --strict 会失败）")
    else:
        print("\n  ✓ 已译文档全部同步、无死链、无残留中文")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
