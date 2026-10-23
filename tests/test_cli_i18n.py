"""命令行语言层：优先级、语言标签归一、错误说明取译。

为什么要有这个测试：命令行的语言是**进程级全局状态**，一旦定错，整个进程的
输出就都是错的语言，而且不会有任何报错 —— 用户只会觉得"翻译没生效"。
几个具体的坑：

  1. `--lang` 若留给 argparse，`product_cli` 的 `parse_known_args` 会把它塞进
     `extra` 透传给节点进程，变成启动失败。所以它必须在解析前被摘掉。
  2. `product_cli` 会再次调用 `a2n_node.__main__.main()`，那层的 argv 是拼出来的。
     若重新解析语言，用户的选择会被环境变量覆盖回去。
  3. `LANG=ja_JP` 不能猜成英文 —— 只有两种语言时，猜错比不猜更糟。

`err_en.py` 是从 `web/i18n.js` 生成的，最后一条测试盯住"别改歪了"。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from a2n_sdk import cli_i18n

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "gen_err_en.py"


@pytest.fixture(autouse=True)
def _clean_locale():
    """每个用例都从"还没定过"开始：语言是进程级全局，不隔离就会互相污染。"""
    cli_i18n.reset_locale()
    yield
    cli_i18n.reset_locale()


def _generator():
    spec = importlib.util.spec_from_file_location("gen_err_en", GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_explicit_flag_beats_every_environment(monkeypatch):
    monkeypatch.setenv("A2N_LANG", "zh-CN")
    monkeypatch.setenv("LC_ALL", "zh_CN.UTF-8")
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    rest = cli_i18n.configure(["--lang", "en", "start"])
    assert cli_i18n.locale() == "en"
    assert rest == ["start"], "摘掉 --lang 之后剩下的参数要原样保留"


def test_environment_order_is_most_specific_first(monkeypatch):
    monkeypatch.delenv("A2N_LANG", raising=False)
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    cli_i18n.configure([])
    assert cli_i18n.locale() == "en", "LC_ALL 比 LANG 更具体"

    cli_i18n.reset_locale()
    monkeypatch.setenv("A2N_LANG", "zh-CN")
    cli_i18n.configure([])
    assert cli_i18n.locale() == "zh-CN", "A2N_LANG 是产品自己的开关，优先级最高"


def test_default_is_chinese_without_any_hint(monkeypatch):
    for name in ("A2N_LANG", "LC_ALL", "LANG"):
        monkeypatch.delenv(name, raising=False)
    cli_i18n.configure([])
    assert cli_i18n.locale() == cli_i18n.DEFAULT == "zh-CN"


@pytest.mark.parametrize("value,expected", [
    ("en_US.UTF-8", "en"),
    ("en", "en"),
    ("EN-gb", "en"),
    ("zh_CN.UTF-8", "zh-CN"),
    ("zh-TW", "zh-CN"),
    # 没有对应语言时回落到默认，而不是猜成英文。
    ("ja_JP.UTF-8", None),
    ("C", None),
    ("POSIX", None),
    ("", None),
])
def test_language_tags_are_normalised(value, expected):
    assert cli_i18n.from_env({"LANG": value}) == expected


def test_flag_is_stripped_in_both_forms():
    assert cli_i18n.split_language(["--lang=en", "start", "--port", "1"]) == ("en", ["start", "--port", "1"])
    assert cli_i18n.split_language(["start", "--lang", "en"]) == ("en", ["start"])
    # 末尾没有取值的 --lang 不能被静默丢掉，否则用户以为切了语言其实没切。
    assert cli_i18n.split_language(["start", "--lang"]) == (None, ["start", "--lang"])


def test_unsupported_language_fails_loudly():
    with pytest.raises(ValueError) as excinfo:
        cli_i18n.configure(["--lang", "fr"])
    message = str(excinfo.value)
    assert message.startswith("CLI_LANG_UNSUPPORTED: "), "错误要带码，才能被统一翻译"
    assert "fr" in message and "en" in message, "要告诉用户可选的取值"


def test_locale_is_decided_once():
    """嵌套入口（product_cli → a2n_node）不能把用户的选择重新解析掉。"""
    cli_i18n.configure(["--lang", "en"])
    assert cli_i18n.locale() == "en"
    # 第二层拿到的 argv 不含 --lang；若重新解析就会掉回默认中文。
    cli_i18n.configure(["serve", "--home", "/tmp/x"])
    assert cli_i18n.locale() == "en"


def test_set_locale_rejects_unknown_and_keeps_state():
    assert cli_i18n.set_locale("en") is True
    assert cli_i18n.locale() == "en"
    assert cli_i18n.set_locale("klingon") is False
    assert cli_i18n.locale() == "en", "拒绝之后不能把当前语言弄丢"


def test_text_falls_back_from_english_to_chinese_to_key(monkeypatch):
    monkeypatch.setitem(cli_i18n.MESSAGES["zh-CN"], "cli.probe", "只有中文")
    monkeypatch.setitem(cli_i18n.MESSAGES["zh-CN"], "cli.both", "两种都有")
    monkeypatch.setitem(cli_i18n.MESSAGES["en"], "cli.both", "Both languages")
    cli_i18n.set_locale("en")
    assert cli_i18n.t("cli.both") == "Both languages"
    assert cli_i18n.t("cli.probe") == "只有中文", "英文缺这条就回退中文，不显示键名"
    assert cli_i18n.t("cli.missing") == "cli.missing", "两边都没有就显示键名，一眼看出漏了"


def test_error_text_translates_known_code_and_keeps_it():
    cli_i18n.set_locale("en")
    text = cli_i18n.error_text("ACCOUNT_ID_REQUIRED: account_id 不能为空")
    assert text == "ACCOUNT_ID_REQUIRED: account_id cannot be empty", "码保留在前面，说明换成英文"


def test_error_text_leaves_unknown_and_uncoded_messages_alone():
    cli_i18n.set_locale("en")
    # 没有登记的码：宁可显示中文，也不猜一个英文。
    assert cli_i18n.error_text("NOT_A_REGISTERED_CODE: 中文说明") == "NOT_A_REGISTERED_CODE: 中文说明"
    # 根本没有码的消息（HTTP 响应里的 error 字段）：原样返回。
    assert cli_i18n.error_text("需要本机管理凭据") == "需要本机管理凭据"


def test_error_text_is_verbatim_in_the_default_locale():
    """默认语言一个字节都不改：日志、脚本解析、既有断言都靠这条。"""
    cli_i18n.set_locale("zh-CN")
    original = "ACCOUNT_ID_REQUIRED: account_id 不能为空"
    assert cli_i18n.error_text(original) == original


def test_generated_error_table_matches_the_web_message_table():
    generator = _generator()
    current = generator.OUTPUT.read_text(encoding="utf-8")
    assert current == generator.render(), (
        "err_en.py 与 web/i18n.js 不同步 —— 运行 python scripts/gen_err_en.py 重新生成")
    assert len(cli_i18n.ERR_MESSAGES) == len(generator.english_errors())


def test_english_table_is_a_translated_subset():
    """与浏览器表同一套不变量：en ⊆ zh-CN、英文里没有汉字、占位符一一对应。"""
    import re
    han = re.compile(r"[\u4e00-\u9fff]")
    placeholder = re.compile(r"\{([A-Za-z0-9_]+)\}")
    chinese, english = cli_i18n.MESSAGES["zh-CN"], cli_i18n.MESSAGES["en"]
    assert not set(english) - set(chinese), "英文有中文表里没有的键（拼错了就永远不生效）"
    untranslated = [k for k, v in english.items() if han.search(v)]
    assert not untranslated, "英文表里还有汉字（这条其实没翻）：" + "、".join(untranslated[:5])
    drift = [(k, sorted(set(placeholder.findall(chinese[k]))),
              sorted(set(placeholder.findall(english[k]))))
             for k in english
             if set(placeholder.findall(chinese[k])) != set(placeholder.findall(english[k]))]
    assert not drift, "占位符对不上（少一个就是界面上少一个值）：" + repr(drift[:3])


def test_every_key_used_in_the_packages_is_defined():
    """源码里 `cli_i18n.t("cli.x")` 用到的键必须在表里，否则终端会打出键名。"""
    import re
    pattern = re.compile(r"""cli_i18n\.t\(\s*['"](cli\.[A-Za-z0-9_.-]+)['"]""")
    used = set()
    for path in (ROOT / "packages").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        used.update(pattern.findall(path.read_text(encoding="utf-8")))
    assert used, "扫描不到任何取词点，正则可能失效了"
    missing = sorted(used - set(cli_i18n.MESSAGES[cli_i18n.DEFAULT]))
    assert not missing, "这些键被用了但表里没有：" + "、".join(missing)


def _run(*args, **kwargs):
    import os
    import subprocess
    return subprocess.run([sys.executable, *args], capture_output=True, text=True,
                          encoding="utf-8", timeout=60,
                          env={**os.environ, "A2N_LANG": ""}, **kwargs)


def test_help_actually_switches_language():
    """真起一个进程跑 --help：这是用户第一次接触产品时会看到的界面。"""
    english = _run("-m", "a2n_node.product_cli", "--lang", "en", "--help")
    assert english.returncode == 0, english.stderr
    assert "Start the full node and its console" in english.stdout
    assert "Output language: --lang zh-CN|en" in english.stdout, "得告诉用户怎么切语言"

    chinese = _run("-m", "a2n_node.product_cli", "--lang", "zh-CN", "--help")
    assert chinese.returncode == 0, chinese.stderr
    assert "启动完整节点与控制台" in chinese.stdout
    assert "输出语言：--lang zh-CN|en" in chinese.stdout


def test_language_flag_works_after_the_subcommand():
    """`a2n-sdk request --help --lang en`：标志在子命令后面也得认。

    `product_cli` 用 `parse_known_args` 把不认识的参数透传给子命令，`--lang`
    若漏进 `extra` 就会变成节点进程的启动参数 —— 这条断言盯的就是那个洞。
    """
    run = _run("-m", "a2n_node.product_cli", "request", "--help", "--lang", "en")
    assert run.returncode == 0, run.stderr
    # 子命令自己的 --help 只打 description（`help=` 那句只在父级的列表里出现）。
    assert "Arguments: --path PATH" in run.stdout


def test_unsupported_language_exits_with_usage_error():
    run = _run("-m", "a2n_node.product_cli", "--lang", "fr")
    assert run.returncode == 2
    assert "CLI_LANG_UNSUPPORTED" in run.stderr
    assert "en" in run.stderr and "zh-CN" in run.stderr
