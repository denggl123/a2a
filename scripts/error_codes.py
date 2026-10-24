"""错误码登记表：从源码里提取「CODE: 说明」。

为什么要有这个脚本：错误码是**跨节点与跨语言的契约**（`docs/I18N.md` 铁律 1、3），
散在 50 多个文件里靠人记必然漂移。这里把它变成机器可校验的东西：

  python scripts/error_codes.py            校验：有中文 raise 没带码就退出 1
  python scripts/error_codes.py --write    重新生成 docs/ERROR-CODES.md（并顺带 --write-en）
  python scripts/error_codes.py --write-en 只生成英文版 docs/ERROR-CODES.en.md
  python scripts/error_codes.py --list     打印全部位置（file:line  CODE  说明）

英文版是**生成**的，不是翻译的：说明文字直接取 `a2n_sdk.err_en`（由
`scripts/gen_err_en.py` 从 `web/i18n.js` 的 `err.*` 生成）。416 行表格手抄一遍
必然和界面文案对不上，而错误说明正是用户最容易同时看到两处的东西。

两处来源：

  1. `raise` 的第一个字符串参数（或 message=/detail= 关键字参数）含中文且不以
     `CODE: ` 开头，就算没带码 —— 这类必须补。协议封套 `self._failure(...)`
     自带码，跳过。
  2. 任何以 `CODE: ` 开头的中文字面量 —— 错误文案可能出现在 `{"error": …}`、
     `CallResponse.failure(…)`、流式响应的 `data.error` 里，逐个 API 去认迟早漏。
     **只收已经带码的**：这些是用户能直接看到的文案，得进登记表才能被 `err.*`
     取译；而诊断用的 `detail` / `reason`（如「对射未达成」）属于状态说明，
     该走「稳定 id + 标签表」，不该硬塞成错误码，所以不在这里强制。
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (ROOT / "packages", ROOT / "scripts")
DOC = ROOT / "docs" / "ERROR-CODES.md"
DOC_EN = ROOT / "docs" / "ERROR-CODES.en.md"

#: 不在码体系内的脚本：它们的中文不是「服务端错误」，加码反而碍事。
#:   - bootstrap.py：安装器输出，每行以 `✗` 开头，是给人看的进度而不是协议错误；
#:   - docker_acceptance.py：验收脚本的断言文本，不是产品输出；
#:   - error_codes.py：本脚本自己的帮助与报错文字。
EXCLUDE = {
    "scripts/bootstrap.py",
    "scripts/docker_acceptance.py",
    "scripts/error_codes.py",
}

CJK = re.compile(r"[\u4e00-\u9fa5]")
CODE = re.compile(r"^([A-Z][A-Z0-9_]{2,63}):\s*")
PREFIX = re.compile(r"^[rbuRBUfF]{0,2}['\"]")


@dataclass(frozen=True)
class Site:
    """一处错误码：哪个文件哪一行、什么码、说明原文。"""

    path: str
    lineno: int
    code: str | None
    message: str

    @property
    def where(self) -> str:
        return f"{self.path}:{self.lineno}"


def _message_arg(call: ast.Call) -> ast.expr | None:
    if call.args:
        return call.args[0]
    for kw in call.keywords:
        if kw.arg in {"message", "detail", "reason", "note"}:
            return kw.value
    return None


def _text_of(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        # f-string 里的插值不展开，只留 `{…}` 占位，够读也够比对。
        # ast.unparse 会还原成 `f'…'`：把前缀与收尾引号去掉，只留正文。
        raw = ast.unparse(node)
        head = PREFIX.match(raw)
        if head:
            quote = raw[head.end() - 1]
            raw = raw[head.end():]
            if raw.endswith(quote):
                raw = raw[:-1]
        return raw
    return None


def scan(roots: tuple[Path, ...] = SOURCES) -> list[Site]:
    """扫出所有「中文 raise」（带码与没带码）+ 已带码的响应字段。"""
    sites: list[Site] = []
    files = sorted(path for root in roots for path in root.rglob("*.py"))
    for path in files:
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        if rel in EXCLUDE:
            continue
        src = path.read_text(encoding="utf-8")
        if not CJK.search(src):
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:  # pragma: no cover - 语法错由别的测试管
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                continue
            func = node.exc.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "?")
            if name == "_failure":
                continue
            arg = _message_arg(node.exc)
            text = _text_of(arg) if arg is not None else None
            if text is None or not CJK.search(text):
                continue
            bare = PREFIX.sub("", text)
            match = CODE.match(bare)
            sites.append(Site(rel, arg.lineno, match.group(1) if match else None, bare))
        sites += _coded_literals(tree, rel, {(s.lineno, s.code) for s in sites if s.path == rel})
    return sites


def _coded_literals(tree: ast.AST, rel: str, seen: set[tuple[int, str]]) -> list[Site]:
    """任何以 `CODE: ` 开头的中文字面量，都算一处登记项。

    只认字面量、不认位置：错误文案可能出现在 `raise`、`{"error": …}`、
    `CallResponse.failure(…)` 里，逐个 API 去认迟早漏。`seen` 用来和 `raise`
    那趟去重（同一个字面量会被两趟都看到）。
    """
    found: list[Site] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Constant, ast.JoinedStr)):
            continue
        text = _text_of(node)
        if text is None or not CJK.search(text):
            continue
        bare = PREFIX.sub("", text)
        match = CODE.match(bare)
        if not match:
            continue
        key = (node.lineno, match.group(1))
        if key in seen:
            continue
        seen.add(key)
        found.append(Site(rel, node.lineno, match.group(1), bare))
    return found


def uncoded(sites: list[Site]) -> list[Site]:
    return [s for s in sites if s.code is None]


def registry(sites: list[Site]) -> dict[str, list[Site]]:
    out: dict[str, list[Site]] = {}
    for site in sites:
        if site.code:
            out.setdefault(site.code, []).append(site)
    return out


def _describe(sites: list[Site]) -> str:
    """同一码的多处说明取最短的一条：最短的通常最不含参数、最适合做通用文案。"""
    texts = sorted({s.message for s in sites}, key=lambda t: (len(t), t))
    # 去掉消息里的 `CODE: ` 前缀（Site.message 存的是原文，比对时方便）
    return CODE.sub("", texts[0])


def render(sites: list[Site]) -> str:
    table = registry(sites)
    groups: dict[str, list[str]] = {}
    for code in table:
        groups.setdefault(code.split("_", 1)[0], []).append(code)
    lines = [
        "# 错误码登记表",
        "",
        "> 本文件由 `python scripts/error_codes.py --write` 生成，不要手改。",
        "> 校验：`python scripts/error_codes.py`（有中文 `raise` 没带码就失败）。",
        "",
        f"共 **{len(table)}** 个码，覆盖 **{len(sites)}** 处消息（`raise` 与已带码的响应字段）。",
        "",
        "## 约定",
        "",
        "- 消息形如 `\"CODE: 中文说明\"`；码是大写蛇形，**永不翻译**，说明随语言变。",
        "- 前端 `I18N.errorText()` 按码取译：默认语言原样返回，其它语言查 `err.<CODE>`。",
        "- 同一个码可以出现在多处，但语义必须一致；「次数」列是出现处数。",
        "",
    ]
    for prefix in sorted(groups):
        lines += [f"## {prefix}", "", "| 码 | 说明 | 次数 |", "| --- | --- | --- |"]
        for code in sorted(groups[prefix]):
            text = _describe(table[code]).replace("|", "\\|")
            lines.append(f"| `{code}` | {text} | {len(table[code])} |")
        lines.append("")
    return "\n".join(lines)


def render_en(sites: list[Site]) -> str:
    """英文版登记表。表结构与中文版逐行对应，说明取 `a2n_sdk.err_en`。

    第一行是同步戳：`docs_i18n_check.py` 靠它判断英文版有没有过期。中文版是
    本脚本生成的，代码一改中文版就变，戳随之对不上 —— 这正是想要的信号。
    """
    from a2n_sdk.err_en import MESSAGES as ERR_EN

    table = registry(sites)
    groups: dict[str, list[str]] = {}
    for code in table:
        groups.setdefault(code.split("_", 1)[0], []).append(code)
    missing = sorted(code for code in table if code not in ERR_EN)
    lines = [
        f"<!-- source: {_stamp()} -->",
        "# Error code registry",
        "",
        "> Generated by `python scripts/error_codes.py --write`. Do not edit by hand.",
        "> Check: `python scripts/error_codes.py` (fails when a Chinese `raise` carries no code).",
        "",
        f"**{len(table)}** codes across **{len(sites)}** message sites (`raise` statements and coded response fields).",
        "",
        "## Convention",
        "",
        "- A message reads `\"CODE: explanation\"`. The code is upper snake case and is **never translated**; the explanation follows the language.",
        "- `I18N.errorText()` looks up by code: the default locale returns the text verbatim, other locales look up `err.<CODE>`.",
        "- One code may appear in several places, but its meaning must stay the same. The \"Sites\" column counts occurrences.",
        "",
    ]
    for prefix in sorted(groups):
        lines += [f"## {prefix}", "", "| Code | Explanation | Sites |", "| --- | --- | --- |"]
        for code in sorted(groups[prefix]):
            text = ERR_EN.get(code) or CODE.sub("", _describe(table[code]))
            lines.append(f"| `{code}` | {text.replace('|', chr(92) + '|')} | {len(table[code])} |")
        lines.append("")
    if missing:
        # 不抛错：缺英文说明由 `scripts/i18n_check.py` 那边统一报，这里只留痕迹。
        print(f"注意：{len(missing)} 个码没有英文说明，暂用中文：{'、'.join(missing[:5])}", file=sys.stderr)
    return "\n".join(lines)


def _stamp() -> str:
    """中文版的同步戳（与 `scripts/docs_i18n_check.py` 同一算法）。"""
    import hashlib

    data = DOC.read_bytes().replace(b"\r\n", b"\n") if DOC.is_file() else b""
    return hashlib.sha256(data).hexdigest()[:12]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="错误码登记表校验 / 生成")
    parser.add_argument("--write", action="store_true", help="重新生成 docs/ERROR-CODES.md")
    parser.add_argument("--write-en", action="store_true", help="只重新生成 docs/ERROR-CODES.en.md")
    parser.add_argument("--list", action="store_true", help="打印每一处位置")
    args = parser.parse_args(argv)

    sites = scan()
    missing = uncoded(sites)

    if args.write:
        # 显式 LF：仓库 .gitattributes 要求 eol=lf，用默认换行会写出 CRLF
        with DOC.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(render(sites))
        print(f"已写入 {DOC.relative_to(ROOT)}：{len(registry(sites))} 个码 / {len(sites)} 处")
        args.write_en = True  # 英文版的戳指向中文版，必须跟着一起重生成
    if args.write_en:
        with DOC_EN.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(render_en(sites))
        print(f"已写入 {DOC_EN.relative_to(ROOT)}")
    if args.list:
        for site in sorted(sites, key=lambda s: (s.path, s.lineno)):
            print(f"{site.where}\t{site.code or '（缺码）'}\t{site.message[:70]}")

    if missing:
        print(f"有 {len(missing)} 处中文 raise 没带错误码：", file=sys.stderr)
        for site in sorted(missing, key=lambda s: (s.path, s.lineno)):
            print(f"  {site.where}  {site.message[:60]}", file=sys.stderr)
        return 1

    print(f"错误码齐全：{len(registry(sites))} 个码 / {len(sites)} 处消息")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
