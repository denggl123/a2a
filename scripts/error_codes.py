"""错误码登记表：从源码里的 `raise` 消息提取「CODE: 说明」。

为什么要有这个脚本：错误码是**跨节点与跨语言的契约**（`docs/I18N.md` 铁律 1、3），
散在 50 多个文件里靠人记必然漂移。这里把它变成机器可校验的东西：

  python scripts/error_codes.py            校验：有中文 raise 没带码就退出 1
  python scripts/error_codes.py --write    重新生成 docs/ERROR-CODES.md
  python scripts/error_codes.py --list     打印全部位置（file:line  CODE  说明）

判定口径：`raise` 的第一个字符串参数（或 message=/detail= 关键字参数）
含中文且不以 `CODE: ` 开头，就算没带码。协议封套 `self._failure(...)` 自带码，跳过。
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
    """扫出所有「中文 raise」，带码与没带码都返回。"""
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
    return sites


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
        f"共 **{len(table)}** 个码，覆盖 **{len(sites)}** 处 `raise`。",
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="错误码登记表校验 / 生成")
    parser.add_argument("--write", action="store_true", help="重新生成 docs/ERROR-CODES.md")
    parser.add_argument("--list", action="store_true", help="打印每一处位置")
    args = parser.parse_args(argv)

    sites = scan()
    missing = uncoded(sites)

    if args.write:
        # 显式 LF：仓库 .gitattributes 要求 eol=lf，用默认换行会写出 CRLF
        with DOC.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(render(sites))
        print(f"已写入 {DOC.relative_to(ROOT)}：{len(registry(sites))} 个码 / {len(sites)} 处")
    if args.list:
        for site in sorted(sites, key=lambda s: (s.path, s.lineno)):
            print(f"{site.where}\t{site.code or '（缺码）'}\t{site.message[:70]}")

    if missing:
        print(f"有 {len(missing)} 处中文 raise 没带错误码：", file=sys.stderr)
        for site in sorted(missing, key=lambda s: (s.path, s.lineno)):
            print(f"  {site.where}  {site.message[:60]}", file=sys.stderr)
        return 1

    print(f"错误码齐全：{len(registry(sites))} 个码 / {len(sites)} 处 raise")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
