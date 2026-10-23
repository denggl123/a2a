"""命令行取词：终端也要能换语言。

为什么单独一层，而不是复用浏览器那张表：控制台的语言状态在浏览器里
（`web/i18n.js`），命令行的语言状态在进程里，两者没有共享的运行时。
但**错误说明是共享的** —— 同一批错误码既出现在界面上，也出现在终端里，
所以英文只写一份：`web/i18n.js` 的 `err.*` 是唯一来源，
`scripts/gen_err_en.py` 把它生成为 `err_en.py`，本模块直接取用。
手抄 379 条进 Python 迟早会和 JS 那份走偏，而走偏的表现是「界面上英文、
终端里中文」，没人会立刻发现。

优先级与浏览器端一致（显式 > 环境 > 默认）：

    --lang en  >  A2N_LANG  >  LC_ALL  >  LANG  >  zh-CN

只认 `zh*` 和 `en*`。`ja_JP.UTF-8` 回落到中文而不是猜成英文 —— 只有两种语言时，
猜错比不猜更糟；`C` / `POSIX` 同理，它们只说明"没有本地化环境"，不代表英文用户。
"""
from __future__ import annotations

import os
import re
import sys

from .err_en import MESSAGES as ERR_MESSAGES

DEFAULT = "zh-CN"
# 从最具体到最宽松：显式设置（LC_ALL）优先于默认区域（LANG）。
ENV_ORDER = ("A2N_LANG", "LC_ALL", "LANG")
LANG_FLAG = "--lang"

#: 命令行自己的文案。键用 `cli.` 前缀，与浏览器的 `rt.*` / `err.*` 分开 ——
#: 两边的键空间本来就不重叠，加前缀是为了将来合并时一眼看出出处。
MESSAGES: dict[str, dict[str, str]] = {
    "zh-CN": {
        # a2n-sdk（完整产品入口）
        "cli.sdk.description": "A2N 完整桌面节点：安装全套、启动、自检",
        "cli.sdk.start.help": "启动完整节点与控制台",
        "cli.sdk.start.background": "后台运行，日志写入节点目录",
        "cli.sdk.start.open": "打开控制台",
        "cli.sdk.doctor.help": "检查全部包、加密存储、完整装配与本机入口",
        "cli.sdk.doctor.extra_args": "doctor 不接受额外参数",
        "cli.sdk.autostart.help": "注册当前用户登录后常驻的完整 SDK",
        "cli.sdk.restore.help": "停机后用恢复口令导入便携备份",
        "cli.sdk.restore.password_file": "从受保护的本机文件读取口令，适用于无终端的单文件程序",
        "cli.sdk.restore.password_prompt": "恢复口令（不回显）：",
        "cli.sdk.request.help": "调用受保护的本机 API",
        "cli.sdk.request.description": "管理参数：--path PATH、--method GET|POST|PUT|DELETE、--body JSON（- 从标准输入读取）、--header NAME:VALUE；由统一 SDK API 命令解析。",
        "cli.sdk.stop.help": "正常停止本机节点",
        "cli.sdk.running": "本机节点已在运行：{url}",
        "cli.sdk.started": "完整 SDK 已启动：{url}\n节点数据和日志：{home}",
        "cli.sdk.failed": "SDK 操作失败：{error}",
        # a2n-node（节点进程）
        "cli.node.description": "A2N 本机节点",
        "cli.node.serve.help": "启动常驻节点与本机管理页",
        "cli.node.port.help": "本机管理页与 A2A 入口端口（默认 8771）",
        "cli.node.public_node.help": "可选的自愿公共节点基础 URL，可重复指定；远端需 HTTPS",
        "cli.node.relay_node.help": "可选的自愿密封中继节点；本机主动领活，无需公网入站，远端需 HTTPS",
        "cli.node.coord_mailbox.help": "指定出站协调邮箱；有局域网公开入口的节点也可借它接受公网发现",
        "cli.node.origin.help": "允许连接本机的远程控制台来源",
        "cli.node.no_p2p.help": "关闭局域网/种子节点发现",
        "cli.node.p2p_port.help": "P2P 发现 UDP 端口",
        "cli.node.bootstrap.help": "P2P 冷启动邻居，可重复指定",
        "cli.node.no_lan_beacon.help": "关闭局域网零配置广播",
        "cli.node.advertise_host.help": "诊断显示的发现地址；回程路由始终使用实测源地址",
        "cli.node.public_base.help": "明确可被其他节点访问的 HTTP 入口；未配置时只发现、不广播供给",
        "cli.node.open.help": "在浏览器打开本机管理页",
        "cli.node.coord_allow.help": "明确允许协调引荐访问的可信网络，可重复；局域网试点例如 192.168.1.0/24",
        "cli.node.start_failed": "A2N 节点启动失败：{error}；检查端口 {port} 或另选 --port",
        "cli.node.invalid_config": "A2N 节点配置无效：{error}",
        "cli.node.started": "A2N 本机节点已启动：{url}",
        "cli.node.p2p": "P2P：UDP {port} · {mode}",
        "cli.node.p2p.advertise": "发现 + 广播供给",
        "cli.node.p2p.discover": "仅发现（未声明对外 HTTP 入口）",
        # a2n-sdk API 命令
        "cli.api.description": "A2N 本机节点 API",
        "cli.api.body.help": "JSON 对象；- 从 stdin 读取",
        # 三个入口共用的语言提示（`--lang` 在 argparse 之前就被摘掉了，
        # 不出现在 options 列表里，所以必须自己说一句）
        "cli.common.language_hint": "输出语言：--lang zh-CN|en，或设置 A2N_LANG；都没有时按 LC_ALL / LANG 判断。",
    },
    "en": {
        "cli.sdk.description": "A2N desktop node: install the full set, start it, run self-checks",
        "cli.sdk.start.help": "Start the full node and its console",
        "cli.sdk.start.background": "Run in the background and write the log into the node directory",
        "cli.sdk.start.open": "Open the console",
        "cli.sdk.doctor.help": "Check every package, encrypted storage, full wiring and the local entry point",
        "cli.sdk.doctor.extra_args": "doctor takes no extra arguments",
        "cli.sdk.autostart.help": "Register the full SDK to run whenever this user signs in",
        "cli.sdk.restore.help": "Import a portable backup with the recovery password while stopped",
        "cli.sdk.restore.password_file": "Read the password from a protected local file; for single-file programs without a terminal",
        "cli.sdk.restore.password_prompt": "Recovery password (not echoed): ",
        "cli.sdk.request.help": "Call the protected local API",
        "cli.sdk.request.description": "Arguments: --path PATH, --method GET|POST|PUT|DELETE, --body JSON (- reads stdin), --header NAME:VALUE; parsed by the shared SDK API command.",
        "cli.sdk.stop.help": "Stop the local node cleanly",
        "cli.sdk.running": "The local node is already running: {url}",
        "cli.sdk.started": "The full SDK is running: {url}\nNode data and log: {home}",
        "cli.sdk.failed": "SDK command failed: {error}",
        "cli.node.description": "A2N local node",
        "cli.node.serve.help": "Start the resident node and the local management page",
        "cli.node.port.help": "Port for the management page and the A2A entry point (default 8771)",
        "cli.node.public_node.help": "Optional volunteer public node base URL; repeatable; the remote side must use HTTPS",
        "cli.node.relay_node.help": "Optional volunteer sealed relay node; this node polls for work, so no inbound public traffic is needed; the remote side must use HTTPS",
        "cli.node.coord_mailbox.help": "Outbound coordination mailbox; a node with a LAN-public entry can also use it to accept public discovery",
        "cli.node.origin.help": "Remote console origins allowed to reach this node",
        "cli.node.no_p2p.help": "Disable LAN and seed-node discovery",
        "cli.node.p2p_port.help": "UDP port for P2P discovery",
        "cli.node.bootstrap.help": "P2P cold-start neighbours; repeatable",
        "cli.node.no_lan_beacon.help": "Disable zero-configuration LAN broadcast",
        "cli.node.advertise_host.help": "Address shown in diagnostics; return routing always uses the measured source address",
        "cli.node.public_base.help": "HTTP entry that other nodes can actually reach; when unset, this node only discovers and does not advertise supply",
        "cli.node.open.help": "Open the local management page in a browser",
        "cli.node.coord_allow.help": "Trusted networks explicitly allowed to reach coordination referrals; repeatable; for a LAN pilot, e.g. 192.168.1.0/24",
        "cli.node.start_failed": "A2N node failed to start: {error}; check port {port} or choose another --port",
        "cli.node.invalid_config": "Invalid A2N node configuration: {error}",
        "cli.node.started": "A2N local node is running: {url}",
        "cli.node.p2p": "P2P: UDP {port} · {mode}",
        "cli.node.p2p.advertise": "discovery + supply broadcast",
        "cli.node.p2p.discover": "discovery only (no public HTTP entry declared)",
        "cli.api.description": "A2N local node API",
        "cli.api.body.help": "JSON object; - reads stdin",
        "cli.common.language_hint": "Output language: --lang zh-CN|en, or set A2N_LANG; otherwise LC_ALL / LANG decides.",
    },
}

#: 语言状态。进程级 —— 一个进程只有一个终端语言。
_locale: str | None = None

_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]+)\}")
#: `CODE: 说明`。码是协议的一部分，翻译后仍然保留在前面（便于排查）。
_CODE = re.compile(r"^([A-Z][A-Z0-9_]{2,63}):\s*(.*)$", re.S)


def supported() -> list[str]:
    """`--lang` 接受哪些取值（与 `MESSAGES` 的键一致）。"""
    return sorted(MESSAGES)


def _match(value: str | None) -> str | None:
    """把 `en_US.UTF-8` 这类取值归一到受支持的语言；不认识返回 None。"""
    if not value:
        return None
    tag = value.strip().lower().replace("_", "-").split(".")[0]
    if tag in MESSAGES:
        return tag
    base = tag.split("-")[0]
    if base == "zh":
        return "zh-CN"
    if base == "en":
        return "en"
    return None


def from_env(environ=None) -> str | None:
    """按 `ENV_ORDER` 找第一个能认出来的环境变量。"""
    env = os.environ if environ is None else environ
    for name in ENV_ORDER:
        hit = _match(env.get(name))
        if hit:
            return hit
    return None


def split_language(argv):
    """摘掉 `--lang X` / `--lang=X`，返回 (取值, 剩余 argv)。

    在 argparse **之前**摘：`product_cli` 用 `parse_known_args` 把不认识的参数
    透传给子命令，`--lang` 若留给 argparse 就会漏进 `extra` 再传给节点进程，
    变成"节点不认识 --lang"的启动失败。留在末尾没有取值的 `--lang` 原样保留，
    让 argparse 报"unrecognized"，而不是被静默丢掉。
    """
    rest: list[str] = []
    picked: str | None = None
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == LANG_FLAG and index + 1 < len(argv):
            picked = argv[index + 1]
            index += 2
            continue
        if item.startswith(LANG_FLAG + "="):
            picked = item[len(LANG_FLAG) + 1:]
            index += 1
            continue
        rest.append(item)
        index += 1
    return picked, rest


def configure(argv=None):
    """入口函数第一步：定一次语言，返回摘掉 `--lang` 的 argv。

    只在第一次调用时定语言 —— `product_cli` 会再调用 `a2n_node.__main__.main()`，
    那层的 argv 是拼出来的、不含用户的 `--lang`，重新解析会把用户的选择覆盖掉。

    取值写了但不认识时抛 `ValueError`（带码）：静默回落到中文会让用户以为
    `--lang fr` 生效了，实际输出还是中文，这种"看起来成功"最难查。
    """
    global _locale
    argv = list(sys.argv[1:] if argv is None else argv)
    picked, rest = split_language(argv)
    if _locale is not None:
        return rest
    if picked is not None:
        matched = _match(picked)
        if matched is None:
            choices = "、".join(supported())
            raise ValueError(f"CLI_LANG_UNSUPPORTED: 不支持的语言：{picked}（可选：{choices}）")
        _locale = matched
    else:
        _locale = from_env() or DEFAULT
    return rest


def locale() -> str:
    return _locale or DEFAULT


def set_locale(name: str) -> bool:
    """测试与嵌入方用。不认识的语言返回 False 且不改状态。"""
    global _locale
    matched = _match(name)
    if matched is None:
        return False
    _locale = matched
    return True


def reset_locale() -> None:
    """回到"还没定过"的状态（测试用，避免用例之间互相污染）。"""
    global _locale
    _locale = None


def _fill(text: str, values: dict) -> str:
    if not values:
        return text
    return _PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), m.group(0))), text)


def t(key: str, **values) -> str:
    """取一条命令行文案：当前语言 → 中文 → 键名本身。

    回落到键名而不是空串：终端里出现 `cli.help.start` 一眼就知道漏了哪条，
    出现空白只会让人以为程序没输出。
    """
    text = MESSAGES.get(locale(), {}).get(key)
    if text is None:
        text = MESSAGES[DEFAULT].get(key)
    if text is None:
        return key
    return _fill(text, values)


def error_text(message) -> str:
    """把 `CODE: 中文说明` 换成当前语言的说明，码保留在前面。

    默认语言原样返回 —— 日志、脚本解析、既有断言都不受影响（与浏览器端
    `I18N.errorText` 同一套语义）。查不到译文的码也原样返回：宁可显示中文，
    也不要显示一个猜出来的英文。

    已知取舍：服务端把参数（端口号、日志路径）拼进了中文说明，换语言后只剩
    通用说明，参数细节丢失。要保留就得让服务端把参数结构化下发，那是协议改动
    —— 与浏览器端阶段 2 记录的是同一个取舍。
    """
    text = str(message)
    if locale() == DEFAULT:
        return text
    hit = _CODE.match(text)
    if not hit:
        return text
    code = hit.group(1)
    translated = ERR_MESSAGES.get(code)
    if not translated:
        return text
    return f"{code}: {translated}"


__all__ = ["DEFAULT", "MESSAGES", "configure", "error_text", "from_env", "locale",
           "reset_locale", "set_locale", "split_language", "supported", "t"]
