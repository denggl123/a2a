"""把 A2N 单体拆成多包仓库（一次性迁移脚本，跑一次即可）。

用法：
    python tools/split_packages.py

产物：
    packages/<dist>/{pyproject.toml, src/a2n_<x>/...}
    pyproject.toml（工作区根：pytest 配置）
    scripts/install_all.sh / .bat
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "a2n"
PKGS = ROOT / "packages"

# ---------------------------------------------------------------- 导入重写规则
# 旧相对导入 / 旧绝对导入 → 新包名。顺序：点多的最先匹配。
RULES: list[tuple[str, str]] = [
    # ---- 旧绝对导入（tests / scripts）----
    (r"from a2n\.adapters\.custodian\.mock import", "from a2n_custodian import"),
    (r"from a2n\.interface\.http\.routers\.", "from a2n_server.routers."),
    (r"from a2n\.domain\.ledger\.service import", "from a2n_ledger import"),
    (r"from a2n\.domain\.identity\.service import", "from a2n_registry import"),
    (r"from a2n\.domain\.dispatch\.service import", "from a2n_dispatch import"),
    (r"from a2n\.domain\.acceptance\.service import", "from a2n_acceptance import"),
    (r"from a2n\.domain\.settlement\.service import", "from a2n_settlement import"),
    (r"from a2n\.domain\.wallet\.service import", "from a2n_wallet import"),
    (r"from a2n\.domain\.task\.service import", "from a2n_task import"),
    (r"from a2n\.domain\.reputation\.service import", "from a2n_reputation import"),
    (r"from a2n\.support\.reconcile import", "from a2n_settlement.reconcile import"),
    (r"from a2n\.support import reconcile", "from a2n_settlement import reconcile"),
    (r"from a2n\.support\.reachability import", "from a2n_registry.reachability import"),
    (r"from a2n\.support\.transport import", "from a2n_transport import"),
    (r"from a2n\.support\.notary\.service import", "from a2n_notary import"),
    (r"from a2n\.support\.market\.service import", "from a2n_market import"),
    (r"from a2n\.kernel\.", "from a2n_kernel."),
    (r"from a2n\.db import", "from a2n_store import"),
    # ---- 四层相对（interface/http/routers）----
    (r"from \.\.\.\.adapters\.custodian\.mock import", "from a2n_custodian import"),
    (r"from \.\.\.\.domain\.ledger\.service import", "from a2n_ledger import"),
    (r"from \.\.\.\.domain\.identity\.service import", "from a2n_registry import"),
    (r"from \.\.\.\.domain\.dispatch\.service import", "from a2n_dispatch import"),
    (r"from \.\.\.\.domain\.task\.service import", "from a2n_task import"),
    (r"from \.\.\.\.domain\.settlement\.service import", "from a2n_settlement import"),
    (r"from \.\.\.\.domain\.wallet\.service import", "from a2n_wallet import"),
    (r"from \.\.\.\.support\.reconcile import", "from a2n_settlement.reconcile import"),
    (r"from \.\.\.\.support import reconcile", "from a2n_settlement import reconcile"),
    (r"from \.\.\.\.support\.reachability import", "from a2n_registry.reachability import"),
    (r"from \.\.\.\.support\.transport import", "from a2n_transport import"),
    (r"from \.\.\.\.support\.notary\.service import", "from a2n_notary import"),
    (r"from \.\.\.\.support\.market\.service import", "from a2n_market import"),
    (r"from \.\.\.\.kernel\.", "from a2n_kernel."),
    (r"from \.\.\.\.db import", "from a2n_store import"),
    # ---- 三层相对（domain/*, support/*, adapters/*）----
    (r"from \.\.\.adapters\.custodian\.mock import", "from a2n_custodian import"),
    (r"from \.\.\.support\.reconcile import", "from a2n_settlement.reconcile import"),
    (r"from \.\.\.support import reconcile", "from a2n_settlement import reconcile"),
    (r"from \.\.\.support\.reachability import", "from a2n_registry.reachability import"),
    (r"from \.\.\.support\.transport import", "from a2n_transport import"),
    (r"from \.\.\.kernel\.", "from a2n_kernel."),
    (r"from \.\.\.db import", "from a2n_store import"),
    # ---- 两层相对（domain 内部互调）----
    (r"from \.\.ledger\.service import", "from a2n_ledger import"),
    (r"from \.\.identity\.service import", "from a2n_registry import"),
    (r"from \.\.dispatch\.service import", "from a2n_dispatch import"),
    (r"from \.\.reputation\.service import", "from a2n_reputation import"),
    (r"from \.\.settlement\.service import", "from a2n_settlement import"),
    (r"from \.\.acceptance import service as acceptance_svc",
     "from a2n_acceptance import judge, policy_ref"),
]

# 纯文本替换（与导入无关的小修小补）
TEXT_RULES: list[tuple[str, str]] = [
    ("acceptance_svc.", ""),
    ('"a2n.interface.http.routers.custodian"', '"a2n_server.routers.custodian"'),
]


def rewrite(text: str) -> str:
    for pat, rep in RULES:
        text = re.sub(pat, rep, text)
    for old, new in TEXT_RULES:
        text = text.replace(old, new)
    return text


# ------------------------------------------------------------------ 包定义
PYPROJECT = """[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "{dist}"
version = "0.1.0"
description = "{desc}"
requires-python = ">=3.11"
dependencies = [{deps}]

[tool.setuptools.packages.find]
where = ["src"]
"""

Package = dict


def pkg(name: str, dist: str, layer: str, desc: str, deps: list[str],
        files: list[tuple[str, str]], init: str) -> Package:
    return dict(name=name, dist=dist, layer=layer, desc=desc, deps=deps,
                files=files, init=init)


SPEC: list[Package] = [
    pkg("a2n_kernel", "a2n-kernel", "L0", "A2N 内核：hash 链、领域事件、策略版本化（零业务零依赖）",
        [], [("kernel/hashing.py", "hashing.py"), ("kernel/events.py", "events.py"),
             ("kernel/policy.py", "policy.py")],
        '''"""a2n-kernel（L0 内核）：所有包都能依赖它，它不依赖任何包。

    hashing  hash 链、canonical JSON、ID 与时钟
    events   进程内领域事件总线（跨模块通信默认走这里）
    policy   策略版本化 PolicyRef + 默认分账规则

这里不允许出现任何业务概念：不认识"任务"，也不认识"钱"。
"""
from .events import publish, subscribe
from .hashing import GENESIS, canonical_json, chain_hash, new_id, now_iso, sha256
from .policy import PolicyRef, split_amount, split_rule

__all__ = ["publish", "subscribe", "GENESIS", "canonical_json", "chain_hash",
           "new_id", "now_iso", "sha256", "PolicyRef", "split_amount", "split_rule"]
'''),

    pkg("a2n_store", "a2n-store", "L0", "A2N 存储：schema、连接与 append-only 触发器",
        [], [("db.py", "db.py")],
        '''"""a2n-store（L0 存储）：唯一的建表与连接出口。

约定：每张表只属于一个包，但建表集中在这里 —— 因为 SQLite 单库、事务必须同库。
生产目标 Postgres 时，这里换成连接池 + 迁移脚本，业务代码不动。
"""
from .db import SCHEMA, TRIGGERS, conn, init_db

__all__ = ["SCHEMA", "TRIGGERS", "conn", "init_db"]
'''),

    pkg("a2n_ledger", "a2n-ledger", "L1", "A2N 账本：append-only 复式记账 + hash 链（唯一真相源）",
        ["a2n-kernel", "a2n-store"], [("domain/ledger/service.py", "service.py")],
        '''"""a2n-ledger（L1 资产）：积分账本。

铁律三在这里落地：
  1. 账本不知道业务 —— 只认 ref_type / ref_id
  2. 没有余额字段   —— 余额由 SUM(delta) 得出，没有"改余额"这个动作
  3. 增发只有一个入口 —— mint/burn 检查调用者身份（唯一白名单 = 持牌方回调）
"""
from .service import Ledger, ensure_account, list_accounts

__all__ = ["Ledger", "ensure_account", "list_accounts"]
'''),

    pkg("a2n_custodian", "a2n-custodian", "L1", "A2N 持牌清算方适配器：唯一能碰钱的包",
        ["a2n-kernel", "a2n-store"],
        [("adapters/custodian/port.py", "port.py"), ("adapters/custodian/mock.py", "mock.py")],
        '''"""a2n-custodian（L1 资产）：持牌清算方适配器。

**全仓库唯一允许与外部资金系统通信的包。** 其余任何包 import 支付 SDK
都会被架构测试判失败（tests/test_architecture.py）。

v1 只有 MockCustodian，接口与真实持牌方对齐后换驱动类即可，业务代码不动。
"""
from .mock import MockCustodian, get_custodian
from .port import CustodianPort

__all__ = ["CustodianPort", "MockCustodian", "get_custodian"]
'''),

    pkg("a2n_registry", "a2n-registry", "L2", "A2N 注册表：A2A 能力登记、KYA、发现与可达性",
        ["a2n-kernel", "a2n-store"],
        [("domain/identity/service.py", "service.py"), ("support/reachability.py", "reachability.py")],
        '''"""a2n-registry（L2 网络）：节点自证的能力，由本包索引并背书。

定位：节点才是权威，注册表只证明"这张卡确实是他发的、当时就是这么写的"。
所以存的是 card_hash、签名校验结果、索引与背书，不是能力本身。

reachability 回答"这一单能不能送进去"——家宽 PC 也能接单的关键。
"""
from .reachability import (ALL_MODES, describe, is_public_url, nat_verdict,
                           normalize_connection, probe_inbound, reachable)
from .service import Registry, card_hash, registry

__all__ = ["Registry", "registry", "card_hash", "reachable", "describe",
           "nat_verdict", "normalize_connection", "probe_inbound", "is_public_url", "ALL_MODES"]
'''),

    pkg("a2n_transport", "a2n-transport", "L2", "A2N 传输阶梯：反向长连接、中继转发、通道协商",
        ["a2n-kernel"], [("support/transport.py", "hub.py")],
        '''"""a2n-transport（L2 网络）：把 p2p 穿透、反向长连接、中继转发收进同一套协商。

    direct / holepunch / tunnel / relay / pull —— 先列全候选，再选优（ICE 思路）

三条边界：打洞只做候选永不做依赖；数据可走直连但证据必须走平台；
中继是唯一 100% 保证连通的通道。
"""
from .hub import FORWARD_TIMEOUT_S, LADDER, Tunnel, TunnelHub, hub, negotiate

__all__ = ["Tunnel", "TunnelHub", "hub", "negotiate", "LADDER", "FORWARD_TIMEOUT_S"]
'''),

    pkg("a2n_reputation", "a2n-reputation", "L2", "A2N 信誉：唯一可用于排序的第三方验证事实",
        ["a2n-kernel", "a2n-store"], [("domain/reputation/service.py", "service.py")],
        '''"""a2n-reputation（L2 网络）：信誉分。

为什么单独成包：发现时六维都可见，但**排序只能用它**——
只有信誉是网络验证过的事实，其余维度都是节点自己说的（price_hint 尤其不参与排序）。
"""
from .service import DefaultWeightedModel, apply_event

__all__ = ["apply_event", "DefaultWeightedModel"]
'''),

    pkg("a2n_dispatch", "a2n-dispatch", "L3", "A2N 调度：候选集、排序策略与派单前校验",
        ["a2n-kernel", "a2n-store", "a2n-registry"], [("domain/dispatch/service.py", "service.py")],
        '''"""a2n-dispatch（L3 业务）：发现自由，派单受控。

发现是信息（谁都能搜），派单是钱（必须校验状态、在线、黑名单、
card_hash 一致性、KYA 等级、并发上限）。
"""
from .service import ASSIGNABLE_STATUS, Discovery, discovery

__all__ = ["Discovery", "discovery", "ASSIGNABLE_STATUS"]
'''),

    pkg("a2n_acceptance", "a2n-acceptance", "L3", "A2N 验收：自动质检、双向计量对账与仲裁入口",
        ["a2n-kernel"], [("domain/acceptance/service.py", "service.py")],
        '''"""a2n-acceptance（L3 业务）：活干得算不算数。

维度有三条性质：可计量、可验证、可复算。只有"可计量 + 可验证"的维度能进分账。
节点自报是内部真相、平台观测是外部真相，谁的单方口径都不采信。
"""
from .service import VARIANCE_TOLERANCE, BaselineSamplePolicy, judge, policy_ref

__all__ = ["judge", "policy_ref", "BaselineSamplePolicy", "VARIANCE_TOLERANCE"]
'''),

    pkg("a2n_settlement", "a2n-settlement", "L3", "A2N 清算：分账指令 + 每日对账（差一分即停）",
        ["a2n-kernel", "a2n-store", "a2n-ledger", "a2n-custodian"],
        [("domain/settlement/service.py", "service.py"), ("support/reconcile.py", "reconcile.py")],
        '''"""a2n-settlement（L3 业务）：分账与对账。

分账只发指令，钱一直在持牌方托管账户内变动（只改归属）。
reconcile 每日核对「积分总量 ≡ 托管余额」，差一分即冻结提现并告警。
"""
from .service import BILLABLE_DIMS, Settlement, compute_amount, settlement

__all__ = ["Settlement", "settlement", "compute_amount", "BILLABLE_DIMS"]
'''),

    pkg("a2n_wallet", "a2n-wallet", "L3", "A2N 钱包：提现申请、风控与积分销毁",
        ["a2n-kernel", "a2n-store", "a2n-ledger", "a2n-custodian"],
        [("domain/wallet/service.py", "service.py")],
        '''"""a2n-wallet（L3 业务）：积分出口。

出口只有一条 —— 提现到实名绑定账户，随即销毁积分。
不做代币、不上链记账、不设二级市场。
"""
from .service import MAX_WITHDRAW_PER_DAY, Wallet, wallet

__all__ = ["Wallet", "wallet", "MAX_WITHDRAW_PER_DAY"]
'''),

    pkg("a2n_task", "a2n-task", "L3", "A2N 任务：状态机、预算冻结、按实结算与事件编排",
        ["a2n-kernel", "a2n-store", "a2n-ledger", "a2n-registry", "a2n-dispatch",
         "a2n-acceptance", "a2n-settlement", "a2n-reputation", "a2n-transport"],
        [("domain/task/service.py", "service.py")],
        '''"""a2n-task（L3 业务）：任务全链路编排。

CREATED → ASSIGNED → SUBMITTED → ACCEPTED → SETTLED
预算冻结 → 执行 → 计量上报 → 按实结算 → 差额退回 → 分账。
"""
from .service import STATE_FLOW, Tasks, tasks

__all__ = ["Tasks", "tasks", "STATE_FLOW"]
'''),

    pkg("a2n_notary", "a2n-notary", "L4", "A2N 公证：凭证链、hash 存证与 Merkle 锚定",
        ["a2n-kernel", "a2n-store"], [("support/notary/service.py", "service.py")],
        '''"""a2n-notary（L4 支撑）：刻章。

纯订阅者：它挂掉不影响任何业务，只影响"章还没刻"，补刻即可 ——
这正是公证层应有的失败语义。
"""
from .service import Notary, notary

__all__ = ["Notary", "notary"]
'''),

    pkg("a2n_market", "a2n-market", "L4", "A2N 行情：只读投影与统计事实（不定价）",
        ["a2n-kernel", "a2n-store", "a2n-ledger"], [("support/market/service.py", "service.py")],
        '''"""a2n-market（L4 支撑）：行情板。

只发行情，不定价：公开的是统计事实（近期成交均价、供给密度、信誉构成），
不是报价，也不做推荐 —— 搜索可以中性，推荐永远不中性。
"""
from .service import stats

__all__ = ["stats"]
'''),

    pkg("a2n_server", "a2n-server", "L5", "A2N 服务端：HTTP 接口装配与管理台",
        ["a2n-kernel", "a2n-store", "a2n-ledger", "a2n-custodian", "a2n-registry",
         "a2n-transport", "a2n-dispatch", "a2n-acceptance", "a2n-settlement",
         "a2n-wallet", "a2n-task", "a2n-notary", "a2n-market",
         "fastapi", "uvicorn"],
        [("interface/http/app.py", "app.py"),
         ("interface/http/routers/custodian.py", "routers/custodian.py"),
         ("interface/http/routers/public.py", "routers/public.py"),
         ("interface/http/routers/registry.py", "routers/registry.py"),
         ("interface/http/routers/tasks.py", "routers/tasks.py"),
         ("interface/http/routers/transport.py", "routers/transport.py"),
         ("interface/http/routers/wallet.py", "routers/wallet.py"),
         ("interface/web/console.html", "web/console.html")],
        '''"""a2n-server（L5 装配）：把各域包拼成一个可运行的服务。

这里只做三件事：路由装配、身份透传（X-Principal）、静态管理台。
业务逻辑一行都不许写 —— 写了就说明某个包缺了 Port。
"""
from .app import app

__all__ = ["app"]
'''),

    pkg("a2n_sdk", "a2n-sdk", "独立", "A2N SDK：给 Agent 与程序调用（零第三方依赖）",
        [], [("../sdk/a2n_sdk/client.py", "client.py"),
             ("../sdk/a2n_sdk/connection.py", "connection.py"),
             ("../sdk/a2n_sdk/console.py", "console.py"),
             ("../sdk/a2n_sdk/runner.py", "runner.py"),
             ("../sdk/a2n_sdk/transport.py", "transport.py"),
             ("../sdk/a2n_sdk/__init__.py", "__init__.py")],
        None),  # SDK 已有自己的 __init__
]


def main() -> None:
    moved: list[str] = []
    for p in SPEC:
        base = PKGS / p["dist"] / "src" / p["name"]
        base.mkdir(parents=True, exist_ok=True)
        for src_rel, dst_rel in p["files"]:
            src = (SRC / src_rel) if not src_rel.startswith("../") else (ROOT / src_rel[3:])
            dst = base / dst_rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not src.exists():
                print(f"  !! 缺失 {src}")
                continue
            if src.suffix == ".py":
                dst.write_text(rewrite(src.read_text(encoding="utf-8")), encoding="utf-8")
            else:
                shutil.copy2(src, dst)
            moved.append(f"{p['dist']}/{dst_rel}")
        if p["init"]:
            (base / "__init__.py").write_text(p["init"], encoding="utf-8")
        # routers/__init__.py
        if p["dist"] == "a2n-server":
            (base / "routers" / "__init__.py").write_text(
                '"""路由包：一个文件一个域，禁止在这里写业务逻辑。"""\n', encoding="utf-8")
        deps = ", ".join(f'"{d}"' for d in p["deps"])
        (PKGS / p["dist"] / "pyproject.toml").write_text(
            PYPROJECT.format(dist=p["dist"], desc=p["desc"], deps=deps), encoding="utf-8")

    # ---- 重写 tests 与 scripts ----
    for rel in ("tests", "scripts"):
        d = ROOT / rel
        if not d.exists():
            continue
        for f in d.rglob("*.py"):
            f.write_text(rewrite(f.read_text(encoding="utf-8")), encoding="utf-8")
            moved.append(f"{rel}/{f.name}")

    # ---- 工作区根 pyproject ----
    (ROOT / "pyproject.toml").write_text('''[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
filterwarnings = ["ignore::DeprecationWarning"]
''', encoding="utf-8")

    # ---- 一键安装脚本 ----
    (ROOT / "scripts" / "install_all.sh").write_text(
        "#!/usr/bin/env bash\n"
        "# 多包一次性可编辑安装。--no-deps：依赖已声明在各自 pyproject 里，\n"
        "# 但 a2n-* 尚未发布到 PyPI，全部本地可编辑安装即可。\n"
        "set -e\ncd \"$(dirname \"$0\")/..\"\n"
        "for d in packages/*/; do\n  echo \"  -> $d\"\n"
        "  python -m pip install -e \"$d\" --no-deps -q\n"
        "done\necho \"全部安装完成\"\n", encoding="utf-8")
    (ROOT / "scripts" / "install_all.bat").write_text(
        "@echo off\r\nREM 多包一次性可编辑安装（Windows）\r\n"
        "for /d %%d in (packages\\*) do (\r\n  echo   -^> %%d\r\n"
        "  python -m pip install -e \"%%d\" --no-deps -q\r\n)\r\n"
        "echo done\r\n", encoding="utf-8")

    print(f"完成：{len(moved)} 个文件，{len(SPEC)} 个包")
    for p in SPEC:
        print(f"  {p['layer']:>3}  {p['dist']:<15} <- {', '.join(p['deps']) or '（无）'}")


if __name__ == "__main__":
    main()
