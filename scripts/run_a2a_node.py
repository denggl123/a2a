"""A2A 协议层节点：本地服务额外挂 /invoke，承接 JSON-RPC message/send 的中继转发。

与 run_node.py 的区别：run_node 只演示平台→隧道的通用中继（/echo）；
本节点是**标准 A2A 客户端能直接调用**的节点——平台的 /a2a/{agent_id}
(message/send) 会把消息经隧道转发到本机 /invoke，就地跑技能并回包。

同一脚本用 A2N_ROLE 起四种**不一样**的节点，让演示里的市场不是四个克隆体：
    A2N_ROLE=charging  华东·按次计费（CNY，对等账户 + 直付渠道）
    A2N_ROLE=free      华北·公益免费（不声明 accepts、不标价）
    A2N_ROLE=x402      新加坡·请求即付（USDC，只收 x402 微支付）
    A2N_ROLE=trial     华南·新上架（前 10 次完成免费，之后毕业才收费）

环境变量：
    A2N_ROLE        预设档位（见上，默认 charging）
    A2N_LOCAL_PORT  本地服务端口（默认 9102）
    A2N_PRINCIPAL   节点主体（默认 acct:bob）
    A2N_SEATS       允许被发现的数量（0/未设 = 不限）：同时最多几个使用者能发现它
    A2N_FREE=1      强制免费（等价 free 档，向后兼容）
    A2N_SKILL       主技能 id（默认 ocr-pro；冒烟靠它找到节点，改前先改冒烟）
    A2N_KEYFILE     身份密钥库（默认 data/keys/a2a_node_<role>.json，不存在就生成）
    A2N_NODE_NAME / A2N_REGION / A2N_LATENCY / A2N_PRICE / A2N_PRICE_CUR
                    逐项覆盖预设（都填 ASCII，中文描述写在预设表里）

关于身份：节点有自己的 ed25519 钥匙（DID = 公钥指纹，不需要谁分配）。它做三件事：
  ① 用这把钥匙**签整张卡**（签名域 = 整卡去掉 sig），把 did/pub/sig 写进
     x-a2n.sovereign —— 这就是"卡自证"：平台发现路与注册路都验它，
     验不过的卡进不了"可直接调用"（P2 卡片自证闸）；
  ② 每次交付用同一把钥匙签一份计量（task_id + node_id + 计费口径），随回包上行；
  ③ 卡上自带 uid（uid 属于签名域，平台补写会让签名失效）。
没有它，计量签名栏永远是"未签名"（宁可如实说未签名，也不塞假签名），
卡也只能是"未自证"。钥匙持久在本地文件里而不是每次进程重建：节点重启不该换一个人。

启动：
    A2N_DB=data/e2e_a2a.db ./.venv/Scripts/python.exe scripts/run_a2a_node.py
"""
from __future__ import annotations

import os
import time
import uuid
from decimal import Decimal
from pathlib import Path

from a2n_p2p import Identity, pub_b64
from a2n_p2p.attest import card_body, sign_metering
from a2n_sdk import Node

LOCAL_PORT = int(os.environ.get("A2N_LOCAL_PORT", "9102"))
PRINCIPAL = os.environ.get("A2N_PRINCIPAL", "acct:bob")
FREE_FORCED = os.environ.get("A2N_FREE") == "1"
SKILL = os.environ.get("A2N_SKILL", "ocr-pro")

# 币种指数：表单按主单位填，卡上按最小单位存 —— 换算只在这里发生
_CUR_EXP = {"CNY": 2, "USD": 2, "EUR": 2, "HKD": 2, "JPY": 0, "USDC": 6}


def _minor(price: str, cur: str) -> int:
    return int((Decimal(price) * (10 ** _CUR_EXP.get(cur, 2))).to_integral_value())


# ---------------------------------------------------------------- 技能实现
def _ocr(payload) -> dict:
    # 标准 A2A 的 text part 进来是字符串，data part 进来是 dict —— 都得接住
    if isinstance(payload, str):
        text, pages = payload, 1
    else:
        text, pages = str((payload or {}).get("text", "")), (payload or {}).get("pages", 1)
    return {"text": text.upper(), "pages": pages}


def _deskew(payload) -> dict:
    body = payload if isinstance(payload, dict) else {}
    return {"skew_deg": body.get("skew_deg", 0.0), "straightened": True,
            "note": "演示实现：只回执纠偏结果，不真的处理图像"}


# 技能目录：id → (展示名, 标签, 处理函数, 计量维度)
SKILL_CATALOG = {
    "ocr-pro": ("OCR 识别", ["ocr", "document", "table"], _ocr,
                [("call_count", "call"), ("page_count", "page")]),
    "doc-deskew": ("文档纠偏", ["ocr", "preprocess"], _deskew,
                   [("call_count", "call")]),
    "ocr-batch": ("批量 OCR", ["ocr", "batch", "high-throughput"], _ocr,
                  [("call_count", "call")]),
}

# ---------------------------------------------------------------- 档位预设
# 验收模板（x-a2n.acceptance_template）：上架时声明"我承诺交付成什么样"。
# 平台拿它跟每次交付比出**可复算的偏差**（结构/完整度/内容），与主观评分分开呈现。
# 按卡声明（一张卡一个模板），所以只有交付形状一致的档位才声明 ——
# free 档同时跑 ocr-pro 与 doc-deskew（两者返回体不同），声明了就会误判，
# 于是它**不声明**，也就诚实地显示"未声明验收模板"（而不是伪造一个 0 偏差）。
_OCR_TEMPLATE = {
    "version": "1.0",
    "required_fields": ["text", "pages"],
    "required_content": [
        {"key": "text_nonempty", "path": "text", "mode": "present"},
        {"key": "at_least_one_page", "path": "pages", "mode": "min", "value": 1},
    ],
}
# 草稿模板：声明了 confidence（实现还没跟上）→ 偏差就是这么来的。
# 硬指标的价值恰恰在这：它会如实说出"你自己声明了、却没交付"。
_OCR_TEMPLATE_DRAFT = {
    "version": "0.9-draft",
    "required_fields": ["text", "pages", "confidence"],
    "required_content": [
        {"key": "text_nonempty", "path": "text", "mode": "present"},
        {"key": "at_least_one_page", "path": "pages", "mode": "min", "value": 1},
    ],
}

PRESETS = {
    "charging": {
        "name": "华东-精算OCR", "region": "cn-east-2",
        "latency": 5000, "availability": 0.95, "concurrent": 4, "sleep": 0.3,
        "price": "0.03", "cur": "CNY", "accepts": ["peer_account", "direct_pay:alipay"],
        "skills": ["ocr-pro"], "tags": ["ocr", "发票", "合同"],
        "template": _OCR_TEMPLATE,
        "desc": "高精度版面还原：发票 / 合同 / 表格，返回结构化字段。按次计费，"
                "对等账户（先用后结）与直付渠道都能结算。",
    },
    "free": {
        "name": "华北-公益OCR", "region": "cn-north-1",
        "latency": 8000, "availability": 0.90, "concurrent": 2, "sleep": 0.2,
        # 允许被发现的数量 = 3：免费档最容易被薅，用它演示"名额间接限制同时使用人数"
        # （满员后新使用者搜不到它，已在用的不受影响；闲置 30 分钟自动释放一个）。
        "seats": 3,
        "price": None, "cur": None, "accepts": [],
        "skills": ["ocr-pro", "doc-deskew"], "tags": ["ocr", "试用", "低价"],
        "template": None,
        "desc": "公益版 OCR：免费开放，适合小批量试用与联调。不承诺 SLA，"
                "别放在产线关键路径上。",
    },
    "x402": {
        "name": "新加坡-极速OCR", "region": "ap-southeast-1",
        "latency": 1500, "availability": 0.99, "concurrent": 8, "sleep": 0.05,
        "price": "0.05", "cur": "USDC", "accepts": ["x402"],
        "skills": ["ocr-pro", "ocr-batch"], "tags": ["ocr", "高频", "海外"],
        "template": _OCR_TEMPLATE,
        "desc": "海外极速节点：请求即付（x402），不用预先建立账户关系，"
                "适合高频小额的流水线场景。",
    },
    # 第四档：**新上架、还在试用期内**的节点。前 10 次完成的调用免费，
    # 额度用尽后毕业才允许收费。留着它，是因为前三档都刻意退出了试用 ——
    # 没有这一档，控制台上的"试用中 N/10 · 免费"徽标就没有真身可看，
    # 试用/毕业这条链路也就演示不出来（界面上只会剩"已毕业 · 收费"）。
    "trial": {
        "name": "华南-新秀OCR", "region": "cn-south-1",
        "latency": 3000, "availability": 0.92, "concurrent": 3, "sleep": 0.25,
        "price": "0.02", "cur": "CNY", "accepts": ["peer_account", "direct_pay:alipay"],
        "skills": ["ocr-pro"], "tags": ["ocr", "新上架", "试用"],
        "template": _OCR_TEMPLATE_DRAFT,
        "desc": "刚上架的节点：前 10 次调用免费（试用期）。验收模板还是草稿版"
                "（声明了 confidence 但实现没跟上），偏差因此非零 —— 这正是"
                "硬指标该说出来的事。",
    },
}

ROLE = os.environ.get("A2N_ROLE", "free" if FREE_FORCED else "charging")
if ROLE not in PRESETS:
    raise SystemExit(f"A2N_ROLE 只认 {sorted(PRESETS)}，收到 {ROLE!r}")
P = dict(PRESETS[ROLE])


def _load_identity(role: str) -> Identity:
    """节点的钥匙：公钥即身份，私钥只在本机。已有就复用，没有就生成。

    持久化而不是每进程新建 —— 节点重启不该换一个人（DID 的全部意义就在这）。
    """
    path = Path(os.environ.get("A2N_KEYFILE") or f"data/keys/a2a_node_{role}.json")
    if path.exists():
        return Identity.load(path)
    ident = Identity.generate()
    ident.save(path)
    print(f"[a2n] 新身份已生成并落盘 {path}（{ident.did}）")
    return ident


IDENT = _load_identity(ROLE)

# 逐项覆盖（全部 ASCII，避免中文过 shell）
NAME = os.environ.get("A2N_NODE_NAME") or P["name"]
REGION = os.environ.get("A2N_REGION") or P["region"]
LATENCY = int(os.environ.get("A2N_LATENCY") or P["latency"])
PRICE = os.environ.get("A2N_PRICE", P["price"] or "")
PRICE_CUR = os.environ.get("A2N_PRICE_CUR", P["cur"] or "")
# 允许被发现的数量（0 / 未设 = 不限）：上架时声明的**分发策略**，不是能力声明。
# 由平台执行（按使用者占名额、闲置自动释放），所以走注册请求参数、不写进卡 ——
# 写进卡会多一个"平台会改"的字段，而平台改写卡会让签名当场失效。
SEATS = int(os.environ.get("A2N_SEATS") or (P.get("seats") or 0))
if FREE_FORCED:
    PRICE, PRICE_CUR, P["accepts"] = "", "", []

SKILL_IDS = [SKILL] + [s for s in P["skills"] if s != SKILL]

X_A2N = {
    "deployment": {"region": REGION},
    # uid 必须**由节点自己带**：它属于卡签名域，平台兜底补写会让签名当场失效
    # （补一个字段 → 整卡哈希变 → 验签对不上）。a2n-registry 的自证闸会因此
    # 拒收"没带 uid 的自签卡"，所以这里一次性写全，再整卡签名。
    "uid": str(uuid.uuid4()),
    # 卡上自证：这个 agent 的钥匙是哪把。平台验计量签名时按**卡上声明的公钥**
    # 来认（a2n_task.service 的第四步："签名用的钥匙与卡上声明的不是同一把"即进争议），
    # 所以这一项必须与下面 attest_fn 用的那个身份是同一把钥匙 —— 两处都来自 IDENT。
    "sovereign": {"did": IDENT.did, "pub": pub_b64(IDENT.pub_raw)},
    # 卡上**不写 trial 字段**：它没有任何开关作用（想被发现的 agent 一律先免费
    # 服务 10 次，卡上退出会被 `validate_card` 直接拒）。前三档（收费/免费/x402）
    # 是用来演示"三条结算通道"的，所以它们在**注册后**由脚本显式补满额度再毕业
    # （见文件末尾 ensure_chargeable）—— 而不是靠在卡上声明退出试用。
    "sla": {"max_latency_ms": LATENCY, "availability_target": P["availability"],
            "max_concurrent": P["concurrent"]},
    "metering": {"dimensions": [
        {"key": k, "unit": u, "verifiable": True}
        for s in SKILL_IDS for k, u in SKILL_CATALOG[s][3]
    ]},
}
if PRICE and PRICE_CUR:
    # v2 价目表：价目事实只在这一处（v1 price_hint 已不再写入）
    X_A2N["price_book"] = {SKILL: {PRICE_CUR: {"dimensions": [
        {"key": "call_count", "amount": _minor(PRICE, PRICE_CUR), "per": 1}]}}}
if P.get("template"):
    X_A2N["acceptance_template"] = P["template"]

CARD = {
    "name": NAME,
    "description": P["desc"],
    "version": "1.0.0",
    "url": None,
    "skills": [{"id": s, "name": SKILL_CATALOG[s][0], "tags": SKILL_CATALOG[s][1],
                "inputModes": ["application/json"], "outputModes": ["application/json"]}
               for s in SKILL_IDS],
    "x-a2n": X_A2N,
}
ACCEPTS = [s.strip() for s in os.environ.get("A2N_ACCEPTS", "").split(",") if s.strip()]
if ACCEPTS:
    CARD["accepts"] = ACCEPTS
elif P["accepts"]:
    CARD["accepts"] = list(P["accepts"])

# 整卡签一次名（必须在**所有**字段都写完、且此后不再改动之后）：
# 签名域是整张卡去掉 sig —— 早签一步，后面补的 accepts / price_book 就不在签名里，
# 那张卡"验签通过"却仍可被人改字段，自证就成了摆设。
X_A2N["sovereign"]["sig"] = IDENT.sign(card_body(CARD))


def local_api(path: str, payload: dict) -> dict:
    """本机服务：平台中继转发进来的所有 POST 都在这里落地。

    /invoke 语义 = 标准 A2A agent 的执行入口：拿到消息就地跑技能，
    返回体就是 A2A Task 的 artifact data（不包信封）；处理不了就抛错，
    平台会把 HTTP 500 如实映射成 failed Task，而不是假装完成。
    """
    if path == "/invoke":
        skill = payload.get("skill") or SKILL
        body = payload.get("payload")
        if body is None and payload.get("message"):
            # 从 A2A message parts 里取 data/text
            for p in payload["message"].get("parts", []):
                if "data" in p:
                    body = p["data"]
                    break
                if "text" in p:
                    body = p["text"]
                    break
        entry = SKILL_CATALOG.get(skill)
        if not entry or skill not in SKILL_IDS:
            raise ValueError(f"unknown skill {skill}")
        time.sleep(P["sleep"])  # 假装在做推理：各档位耗时不一样，好比较
        return entry[2](body or {})
    if path == "/echo":
        return {"echo": payload, "host": f"本机（{NAME}）", "ts": time.time()}
    return {"error": "unknown path", "path": path}


HANDLERS = {s: SKILL_CATALOG[s][2] for s in SKILL_IDS}


def _attest(task_id: str, node_id: str, dims: dict):
    """计量连署：用节点自己的钥匙签"任务号 + 节点号 + 计费口径"。

    node_id 是平台注册时发的 agent_id，不是 DID —— 两个 ID 空间各管各的事：
    agent_id 用于平台内寻址，DID/pub 用于"这把钥匙是谁"。卡上的 sovereign
    把两者绑在一起，平台据此验"签名的钥匙确实是这个 agent 的"。
    """
    return sign_metering(IDENT, task_id=task_id, node_id=node_id, dims=dims)


if __name__ == "__main__":
    node = Node(CARD, HANDLERS, principal=PRINCIPAL,
                base_url=os.environ.get("A2N_BASE", "http://127.0.0.1:8000"),
                discover_limit=SEATS or None,
                attest_fn=_attest)
    price_txt = f"{PRICE} {PRICE_CUR}/次" if PRICE else "免费"
    seat_txt = f" · 名额 {SEATS}" if SEATS else ""
    print(f"[a2n] A2A 节点启动：{NAME}（{ROLE} · {REGION} · {price_txt}{seat_txt} · Ctrl+C 退出）")
    node.serve(console=False, local_agent=(LOCAL_PORT, local_api))
