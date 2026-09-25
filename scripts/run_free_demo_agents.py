"""Six callable, voluntarily free fixtures for the seller console.

The provider (this local script) *chooses* not to charge — a present fact, stated as
such. Never phrase it as a promise of permanence: VISION §5.1 / §7 forbid the platform
from saying "永久免费", because "永久" is a promise while "不收费" is a fact, and a
local script can be stopped at any moment. The card description is buyer-facing copy.

Run locally: python scripts/run_free_demo_agents.py --base http://127.0.0.1:8011
Keys persist under --state-dir; restarting reuses the listings, not new identities.
Default visibility is private: seller-maintenance fixtures do not change public
discovery examples. Pass --visibility public only to publish them to discovery.
These are deterministic utility examples, not model inference. No invented latency,
ratings, reputation or forced trial graduation; no price book keeps them uncharged
after graduation too.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import queue
import re
import threading
import uuid
from pathlib import Path

from a2n_p2p import Identity, pub_b64
from a2n_p2p.attest import card_body, sign_metering
from a2n_sdk import Client, Node

REGISTRATION_LOCK = threading.Lock()


def text_of(payload):
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        return str(payload.get("text", ""))
    raise ValueError('传入文本，或 {"text": "..."}')


def clean_lines(payload):
    lines = [line.strip() for line in text_of(payload).splitlines()]
    return {"text": "\n".join(line for line in lines if line), "mode": "trim"}


def deduplicate_lines(payload):
    lines = [line.strip() for line in text_of(payload).splitlines() if line.strip()]
    unique = list(dict.fromkeys(lines))
    return {"text": "\n".join(unique), "removed": len(lines) - len(unique), "mode": "deduplicate"}


def tidy_paragraphs(payload):
    paragraphs = re.split(r"\n\s*\n", text_of(payload).strip())
    return {"text": "\n\n".join(re.sub(r"\s+", " ", p).strip() for p in paragraphs if p.strip()),
            "mode": "paragraphs"}


def format_json(payload):
    source = text_of(payload)
    value = json.loads(source)
    return {"text": json.dumps(value, ensure_ascii=False, indent=2),
            "type": type(value).__name__}


def preview_csv(payload):
    rows = list(csv.reader(io.StringIO(text_of(payload))))
    return {"headers": rows[0] if rows else [], "rows": rows[1:11],
            "row_count": max(0, len(rows) - 1), "preview_limit": 10}


def extract_links(payload):
    links = re.findall(r"https?://[^\s<>\"'\u3000]+", text_of(payload))
    links = list(dict.fromkeys(link.rstrip(".,;!?，。；！？）)]") for link in links))
    return {"links": links, "count": len(links), "fetches_remote_pages": False}


# Similar offers deliberately share a skill ID but have distinct descriptions.
PROFILES = [
    ("trim", "测试·文本清理｜空行版", "text-clean", "文本清理",
     "删除空行、清理每行首尾空白；不改变行的顺序。适合日志、粘贴文字与提示词预处理。",
     ["文本", "空行", "清理"], clean_lines),
    ("deduplicate", "测试·文本清理｜去重版", "text-clean", "文本清理",
     "删除重复行，保留首次出现的顺序，同时返回删除条数。适合清单、关键词与批量文本去重。",
     ["文本", "去重", "清单"], deduplicate_lines),
    ("paragraphs", "测试·文本清理｜段落版", "text-clean", "文本清理",
     "合并段内多余空白和断行，保留空行分隔的段落。不做语义改写，也不生成新内容。",
     ["文本", "段落", "排版"], tidy_paragraphs),
    ("json", "测试·JSON 格式化", "json-format", "JSON 格式化",
     "校验 JSON 并输出两空格缩进的格式化文本，保留中文字符；格式错误会如实返回失败。",
     ["JSON", "开发", "格式化"], format_json),
    ("csv", "测试·CSV 数据预览", "csv-preview", "CSV 数据预览",
     "解析带表头的 CSV，返回列名、总行数与前 10 行，支持引号中的逗号。不分析或上传数据。",
     ["CSV", "表格", "预览"], preview_csv),
    ("links", "测试·链接提取", "link-extract", "链接提取",
     "从文字中提取 HTTP / HTTPS 链接并去重，保留出现顺序；只解析文本，不访问链接。",
     ["链接", "文本", "提取"], extract_links),
]


def build_card(profile, identity):
    slug, name, skill, skill_name, description, tags, _handler = profile
    card = {
        "name": name, "description": description + " 供给方自愿公益 · 不收费 · 本地确定性测试服务，非模型推理。",
        "version": "1.0.0", "url": None,
        "skills": [{"id": skill, "name": skill_name, "description": description,
                    "tags": tags, "inputModes": ["text/plain", "application/json"],
                    "outputModes": ["application/json"]}],
        "x-a2n": {
            "uid": str(uuid.uuid5(uuid.NAMESPACE_URL, identity.did + "/free-demo/" + slug)),
            "deployment": {"region": "local-demo"},
            "metering": {"dimensions": [{"key": "call_count", "unit": "call", "verifiable": True}]},
            "sovereign": {"did": identity.did, "pub": pub_b64(identity.pub_raw)},
        },
    }
    card["x-a2n"]["sovereign"]["sig"] = identity.sign(card_body(card))
    return card


class ReusableDemoClient(Client):
    """幂等上架 + 序列化：六个首次初始化不要互相抢。

    幂等逻辑本身在 SDK（`Client.register_or_update`），这里只保留这层串行锁 ——
    "同一个 uid 已存在就更新而不是插新条目"是所有节点的通用行为，不该只在演示脚本里。
    """

    def register(self, card, visibility="public", discover_limit=None):
        # The local fixture account is first created during registration; serialize
        # this startup step rather than racing six first-time account initializations.
        with REGISTRATION_LOCK:
            return self.register_or_update(card, visibility, discover_limit)


def serve_profile(profile, args, port):
    slug, name, skill, _skill_name, _description, _tags, handler = profile
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
    print(f"[free-demo] {name} · 自愿公益不收费 · 本地端口 {port}", flush=True)
    node.serve(console=False, local_agent=(port, local_api))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8011")
    parser.add_argument("--principal", default="acct:free-demo")
    parser.add_argument("--visibility", choices=("private", "unlisted", "public"), default="private")
    parser.add_argument("--port-base", type=int, default=9230)
    parser.add_argument("--state-dir", default="data/free-demo-agents")
    args = parser.parse_args()
    errors = queue.Queue()

    def worker(profile, port):
        try:
            serve_profile(profile, args, port)
        except Exception as exc:
            errors.put((profile[0], exc))

    for index, profile in enumerate(PROFILES):
        threading.Thread(target=worker, args=(profile, args.port_base + index),
                         name="free-demo-" + profile[0], daemon=True).start()
    try:
        slug, exc = errors.get()
        raise RuntimeError(f"{slug} 测试节点启动或运行失败") from exc
    except KeyboardInterrupt:
        print("\n免费测试节点已停止；上架信息和本地身份保留，重启可复用。")


if __name__ == "__main__":
    main()
