"""市场演示：几个**可售卖**的本地 agent，让发现页有多个分类、且一格价格里出现多种币种。

与 ``run_free_demo_agents.py`` 的区别就一条，但很关键：

* 那边的夹具是**自愿免费**的，卡上不写价目表（"不收费"是当下事实）；
* 这里的收费夹具会写 ``price_book``，而且是**两条独立挂牌**（CNY + USDC）。

两个币种不是"一个价按汇率算两遍"，是**两条各自独立的挂牌**：网络不做换算、
也不跨币种相加（金额单一源 `a2n_settlement.price`）。所以同时挂两种币是供给方的
选择，不是平台替它编出来的汇率。控制台价格列把它们并排显示，主价在前、其余小字。

免费那几条沿用免费夹具的措辞纪律：说"不收费"（当下事实）可以，说"永久"（对未来的
承诺）不行（VISION §5.1 / §7）。卡片 description 是**买家可见的对外文案**。

本地跑：``python scripts/run_market_demo_agents.py --base http://127.0.0.1:8000``

钥匙存在 ``--state-dir`` 下，上架的 ``uid`` 由钥匙派生 —— 重启**复用同一条上架**，
不会像随机 uid 那样每次都多注册一条。
"""
from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import uuid
from decimal import Decimal
from pathlib import Path

from a2n_custodian.media import by_currency
from a2n_p2p import Identity, pub_b64
from a2n_p2p.attest import card_body, sign_metering
from a2n_sdk import Client, Node

# 同目录脚本：复用已经测过的纯函数（free_demo_check.py 也是这么取 PROFILES 的）
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_free_demo_agents import (  # noqa: E402
    ReusableDemoClient, clean_lines, deduplicate_lines, extract_links,
    format_json, preview_csv, text_of,
)

FREE_SUFFIX = " 供给方自愿公益 · 不收费 · 本地确定性测试服务，非模型推理。"
PAID_SUFFIX = " 供给方自主定价 · 本地确定性测试服务，非模型推理。"


def _minor(amount: str, currency: str) -> int:
    """主单位 → 最小单位整数。

    精度的事实源在持牌层 ``a2n_custodian.media``（"钱以什么形态存在"只在那里回答）。
    这里**不许**再抄一张 {币种: 小数位} 表 —— 抄一份就多一个会漂移的真相。
    """
    media = by_currency(currency)
    if not media:
        raise ValueError(f"未注册的币种：{currency}（媒介注册表里没有它）")
    exp = int(media[0]["exponent"])
    return int((Decimal(amount) * (10 ** exp)).to_integral_value())


def minify_json(payload):
    """压成单行紧凑 JSON；非法输入如实报错，不假装成功。"""
    body = payload if isinstance(payload, dict) else {"text": payload}
    obj = json.loads(body.get("text", ""))
    return {"text": json.dumps(obj, ensure_ascii=False, separators=(",", ":")),
            "minified": True}


# slug, 展示名, 技能 id, 技能展示名, 挂牌价 {币种: 主单位单价}（None = 免费）, 描述, 标签, 处理函数
MARKET = [
    ("text-tidy", "文本清理 · 标准版", "text-clean", "文本清理",
     {"CNY": "0.01", "USDC": "0.0015"},
     "删除空行、清理每行首尾空白；不改变行的顺序。适合日志、粘贴文字与提示词预处理。",
     ["文本", "清理", "预处理"], clean_lines),
    ("text-dedupe", "文本清理 · 去重版", "text-clean", "文本清理", None,
     "删除重复行，保留首次出现的顺序，同时返回删除条数。适合清单、关键词与批量文本去重。",
     ["文本", "去重", "清单"], deduplicate_lines),
    ("json-pretty", "JSON 格式化 · 专业版", "json-format", "JSON 格式化",
     {"CNY": "0.02", "USDC": "0.003"},
     "校验 JSON 并输出两空格缩进的格式化文本，保留中文字符；格式错误会如实返回失败。",
     ["JSON", "格式化", "开发"], format_json),
    ("json-minify", "JSON 压缩 · 免费版", "json-format", "JSON 压缩", None,
     "把 JSON 压成单行紧凑形式、去掉可省略的空白；格式错误会如实返回失败。",
     ["JSON", "压缩", "开发"], minify_json),
    ("csv-peek", "CSV 数据预览 · 专业版", "csv-preview", "CSV 数据预览",
     {"CNY": "0.015", "USDC": "0.002"},
     "解析带表头的 CSV，返回列名、总行数与前 10 行，支持引号中的逗号。不分析或上传数据。",
     ["CSV", "表格", "预览"], preview_csv),
    ("link-grab", "链接提取 · 免费版", "link-extract", "链接提取", None,
     "从文字中提取 HTTP / HTTPS 链接并去重，保留出现顺序；只解析文本，不访问链接。",
     ["链接", "提取", "文本"], extract_links),
]

# 收费档要付得起才算"在卖"：走对等账户（先用后结）或直付渠道。
# 不声明的话门禁会退化成"只能建对等账户配对"，演示里没人能直接下单。
PAID_ACCEPTS = ["peer_account", "direct_pay:alipay"]


def build_card(profile, identity):
    slug, name, skill, skill_name, prices, description, tags, _handler = profile
    x_a2n = {
        # uid 由钥匙派生：重启必须复用同一条上架，而不是再插一条新的
        "uid": str(uuid.uuid5(uuid.NAMESPACE_URL, identity.did + "/market/" + slug)),
        "deployment": {"region": "local-demo"},
        "metering": {"dimensions": [{"key": "call_count", "unit": "call", "verifiable": True}]},
        "sovereign": {"did": identity.did, "pub": pub_b64(identity.pub_raw)},
    }
    if prices:
        # 多币种 = 多条独立挂牌，各按自己的币种精度换算，互不换算
        x_a2n["price_book"] = {skill: {
            cur: {"dimensions": [{"key": "call_count", "amount": _minor(val, cur), "per": 1}]}
            for cur, val in prices.items()}}
    card = {
        "name": name,
        "description": description + (PAID_SUFFIX if prices else FREE_SUFFIX),
        "version": "1.0.0",
        "url": None,
        "skills": [{"id": skill, "name": skill_name, "description": description,
                    "tags": tags, "inputModes": ["text/plain", "application/json"],
                    "outputModes": ["application/json"]}],
        "x-a2n": x_a2n,
    }
    if prices:
        card["accepts"] = list(PAID_ACCEPTS)
    card["x-a2n"]["sovereign"]["sig"] = identity.sign(card_body(card))
    return card


def price_text(prices) -> str:
    return " / ".join(f"{val} {cur}/次" for cur, val in (prices or {}).items()) or "免费"


def serve_profile(profile, args, port):
    slug, name, skill, _skill_name, prices, _description, _tags, handler = profile
    key_path = Path(args.state_dir) / f"{slug}.key.json"
    if key_path.exists():
        identity = Identity.load(key_path)
    else:
        identity = Identity.generate()
        identity.save(key_path)

    def local_api(path, payload):
        if path != "/invoke":
            raise ValueError("unknown path")
        if payload.get("skill") not in (None, "", skill):
            raise ValueError("unknown skill")
        body = payload.get("payload")
        if body is None:
            for part in (payload.get("message") or {}).get("parts", []):
                if "data" in part or "text" in part:
                    body = part.get("data") if "data" in part else part["text"]
                    break
        if len(text_of(body)) > 100_000:
            raise ValueError("测试服务最多接收 100000 字符")
        return handler(body)

    def attest(task_id, node_id, dims):
        return sign_metering(identity, task_id=task_id, node_id=node_id, dims=dims)

    node = Node(build_card(profile, identity), {skill: handler},
                principal=args.principal, base_url=args.base, visibility=args.visibility,
                attest_fn=attest)
    node.client = ReusableDemoClient(args.base, principal=args.principal)
    print(f"[market] {name} · {price_text(prices)} · 本地端口 {port}", flush=True)
    node.serve(console=False, local_agent=(port, local_api))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--principal", default="acct:market-demo")
    parser.add_argument("--visibility", choices=("private", "unlisted", "public"),
                        default="public")
    parser.add_argument("--port-base", type=int, default=9250)
    parser.add_argument("--state-dir", default="data/market-demo-agents")
    args = parser.parse_args()
    errors: queue.Queue = queue.Queue()

    def worker(profile, port):
        try:
            serve_profile(profile, args, port)
        except Exception as exc:
            errors.put((profile[0], exc))

    for index, profile in enumerate(MARKET):
        threading.Thread(target=worker, args=(profile, args.port_base + index),
                         name="market-" + profile[0], daemon=True).start()
    try:
        slug, exc = errors.get()
        raise RuntimeError(f"{slug} 市场节点启动或运行失败") from exc
    except KeyboardInterrupt:
        print("\n市场测试节点已停止；上架信息与本地身份保留，重启可复用。")


if __name__ == "__main__":
    main()
