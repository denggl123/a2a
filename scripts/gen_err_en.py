"""从 `web/i18n.js` 生成 `a2n_sdk/err_en.py` —— 错误说明的英文只写一份。

为什么生成而不是手抄：379 条错误说明已经在控制台那份表里写好了（阶段 5），
命令行要用同一批英文。手抄一份进 Python，两份就会各自漂移；而漂移的表现是
「界面上英文、终端里中文」，几乎没人会同时看两处。

用法：
    python scripts/gen_err_en.py            # 重新生成
    python scripts/gen_err_en.py --check    # 只检查是否同步（退出 1 表示不一致）

改英文说明请改 `web/i18n.js` 的 `err.*`，然后重跑本脚本。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "scripts" / "i18n_check.py"
OUTPUT = ROOT / "packages" / "a2n-sdk" / "src" / "a2n_sdk" / "err_en.py"
ERR_PREFIX = "err."

HEADER = '''"""命令行错误说明（英文）。由 scripts/gen_err_en.py 从 web/i18n.js 生成，勿手改。

改英文说明请改 packages/a2n-sdk/src/a2n_sdk/web/i18n.js 的 `err.*`，再运行
    python scripts/gen_err_en.py
`tests/test_cli_i18n.py` 会比对本文件是否与消息表同步。
"""

MESSAGES = {
'''


def _audit():
    """复用消息表的解析器，避免两处各写一份正则、各自解析出不同的东西。"""
    spec = importlib.util.spec_from_file_location("i18n_check", AUDIT)
    if spec is None or spec.loader is None:
        # 开发者工具链的内部断言，不进错误码登记表（那是给用户看的协议码）。
        raise RuntimeError(f"scripts/i18n_check.py is missing: {AUDIT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def english_errors() -> dict[str, str]:
    """`{CODE: 英文说明}`，按码排序，保证生成结果稳定。"""
    audit = _audit()
    tables = audit.parse_tables(audit.TABLE.read_text(encoding="utf-8"))
    english = tables.get("en", {})
    return {key[len(ERR_PREFIX):]: value
            for key, value in sorted(english.items()) if key.startswith(ERR_PREFIX)}


def render() -> str:
    rows = "".join(f"    {json.dumps(code)}: {json.dumps(text, ensure_ascii=False)},\n"
                   for code, text in english_errors().items())
    return HEADER + rows + "}\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成命令行错误说明")
    parser.add_argument("--check", action="store_true", help="只检查是否与消息表同步")
    args = parser.parse_args(argv)
    wanted = render()
    current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else None
    if args.check:
        if current != wanted:
            print(f"  ✗ {OUTPUT.relative_to(ROOT)} 与 web/i18n.js 不同步，"
                  f"运行 python scripts/gen_err_en.py 重新生成", file=sys.stderr)
            return 1
        count = len(english_errors())
        print(f"  ✓ err_en.py 与消息表同步（{count} 个码）")
        return 0
    OUTPUT.write_text(wanted, encoding="utf-8", newline="\n")
    print(f"  已生成 {OUTPUT.relative_to(ROOT)}（{len(english_errors())} 个码）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
