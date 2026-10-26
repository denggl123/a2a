"""运维脚本不能 grep 本地化过的命令行文案。

`node_console_check.sh` 曾经用 `grep 'A2N 本机节点已启动'` 判断节点起没起来。命令行
有了中英两套文案之后（`cli.node.started` 的 zh 和 en 两份，按 `--lang` / `A2N_LANG` /
`LC_ALL` / `LANG` 选），这个 grep 在任何 locale 解析成英文的机器上都会失败：节点起得
好好的，脚本报「节点没起来」，还打印一行看起来像启动成功的日志。而那台机器恰好就是
英文读者会用的那种 —— 检查工具本身成了最不该出错的地方。

断言看源码而不是跑一遍脚本：跑一遍要起节点、开浏览器，几十秒；这里要守的是
「检测条件与语言无关」，读一眼就能回答。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CJK = re.compile(r"[\u4e00-\u9fa5]")


def _log_greps() -> list[tuple[str, int, str]]:
    """所有「grep 某个日志文件」的脚本行：这些行的输入来自会换语言的进程。"""
    found = []
    for script in sorted((ROOT / "scripts").glob("*.sh")):
        for number, line in enumerate(script.read_text(encoding="utf-8").splitlines(), 1):
            if "grep" in line and ".log" in line:
                found.append((script.name, number, line))
    return found


def test_no_shell_script_greps_a_localised_log_line():
    greps = _log_greps()
    assert greps, "找不到任何日志 grep：要么脚本改名了，要么这条断言已经失效"
    offenders = [f"{name}:{number} {line.strip()}"
                 for name, number, line in greps if CJK.search(line)]
    assert not offenders, (
        "这些行用中文文案判断命令行输出，英文 locale 下会误判：\n  "
        + "\n  ".join(offenders))
