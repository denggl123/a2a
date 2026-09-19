"""架构约束测试：分包之后，纪律由机器执行，不靠自觉。

为什么需要它：所有包都装在同一个环境里，Python 本身拦不住
"a2n-ledger 偷偷 import a2n-custodian"。分包的价值在于**依赖关系被显式声明**，
这个测试就是把声明变成硬约束：

  1. 只能 import 自己 pyproject 里声明过的 a2n-* 包
  2. 依赖图必须无环（有环就说明抽象边界错了，例如当年的 registry ↔ transport）
  3. SDK 必须零依赖（它要跑在别人的机器上）
  4. 只有 a2n-custodian 允许触碰外部资金系统

改一个 pyproject 就能合法地新增依赖 —— 代价是这次改动会被 review 到。
"""
from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "packages"


def _packages() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for d in sorted(PACKAGES.iterdir()):
        pp = d / "pyproject.toml"
        if not pp.exists():
            continue
        meta = tomllib.loads(pp.read_text(encoding="utf-8"))["project"]
        out[meta["name"]] = {
            "dir": d,
            "deps": [x for x in meta.get("dependencies", []) if x.startswith("a2n-")],
        }
    return out


def _imported(dist_dir: Path) -> set[str]:
    """包内源码真实 import 的 a2n-* 顶层模块名（a2n-xxx → a2n_xxx）。

    用 ast 而不是正则：docstring / 注释里的示例代码（如 SDK 用法文档）
    不是 import，不该被当成违规；相对导入（from .client）是包内部，天然合法。
    """
    names: set[str] = set()
    for f in dist_dir.glob("src/**/*.py"):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.startswith("a2n_"):
                        names.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module and node.module.startswith("a2n_"):
                    names.add(node.module.split(".")[0])
    return names


def _dist_to_module(dist: str) -> str:
    return dist.replace("-", "_")


PKGS = _packages()


def test_packages_discovered():
    assert len(PKGS) >= 15, f"包数量不对：{list(PKGS)}"


@pytest.mark.parametrize("dist", sorted(PKGS))
def test_only_declared_dependencies_are_imported(dist: str):
    """import 了没声明的包 = 架构违规。"""
    info = PKGS[dist]
    declared = {_dist_to_module(d) for d in info["deps"]}
    imported = _imported(info["dir"]) - {_dist_to_module(dist)}
    illegal = imported - declared
    assert not illegal, (
        f"{dist} 引用了未声明的包 {sorted(illegal)}。"
        f"请在该包 pyproject.toml 的 dependencies 里显式声明，或改用依赖注入/事件解耦。"
    )


def test_dependency_graph_is_acyclic():
    """有环 = 抽象边界画错了。函数级 import 也算环，只是它躲到了运行时。"""
    graph = {d: set(v["deps"]) for d, v in PKGS.items()}
    state: dict[str, int] = {}

    def visit(n: str, path: list[str]) -> None:
        if state.get(n) == 2:
            return
        if state.get(n) == 1:
            raise AssertionError("依赖成环：" + " → ".join(path + [n]))
        state[n] = 1
        for m in sorted(graph.get(n, ())):
            visit(m, path + [n])
        state[n] = 2

    for n in sorted(graph):
        visit(n, [])


def test_sdk_is_dependency_free():
    """SDK 要装到别人的机器上，一个第三方依赖都不能有。"""
    info = PKGS["a2n-sdk"]
    assert not info["deps"], f"a2n-sdk 不该依赖任何 a2n 包：{info['deps']}"
    assert not _imported(info["dir"]), "a2n-sdk 不许 import 平台内部包"
    pyproject = (info["dir"] / "pyproject.toml").read_text(encoding="utf-8")
    deps = tomllib.loads(pyproject)["project"].get("dependencies", [])
    assert deps == [], f"a2n-sdk 必须零第三方依赖，当前：{deps}"


def test_only_custodian_touches_money_system():
    """铁律二「只刻章不碰钱」在代码层的投影：外部资金调用只出现在 a2n-custodian。"""
    forbidden = re.compile(r"\b(alipay|wechatpay|wxpay|stripe|unionpay|paypal)\b", re.I)
    offenders = []
    for dist, info in PKGS.items():
        if dist == "a2n-custodian":
            continue
        for f in info["dir"].glob("src/**/*.py"):
            if forbidden.search(f.read_text(encoding="utf-8")):
                offenders.append(f"{dist}:{f.name}")
    assert not offenders, f"只有 a2n-custodian 可以接触资金系统，违规：{offenders}"


def test_kernel_has_no_business_concepts():
    """内核不许长出业务：认识'任务'或'分账'的那一刻，它就不再是内核。"""
    banned = re.compile(r"\b(task|settlement|withdraw|escrow)\b", re.I)
    for f in (PACKAGES / "a2n-kernel" / "src").glob("**/*.py"):
        hits = [w for w in banned.findall(f.read_text(encoding="utf-8"))]
        assert not hits, f"a2n-kernel 出现业务概念 {set(hits)}（{f.name}）"


def test_user_facing_copy_never_promises_permanence():
    """VISION §5.1 / §7 / §附决定 3：**网络从不说「永久免费」**。

    「有人自愿永久免费」是**允许的类别**，错的是**措辞**：卡片描述与控制台文案是
    买家可见的对外文案，而「永久」是**承诺**、不是事实 —— 本地演示脚本随时可停，
    这个承诺兑不了。被传开的必须是事实（「这里很多 agent 都能白试」），不是承诺；
    两者只在第一批 agent 毕业收费那天显形：一个是「试用结束了」，另一个是「被背叛了」。

    为什么补这条：2026-09-19 这个词真被写进了六个演示夹具的卡片描述，
    而项目里**一条守卫都没有** —— 纲领写了、代码没拦，正是本项目最不该有的缝。
    用 ast 而不是全文本匹配：注释天然被忽略，docstring（纲领自己就要解释这个词）
    显式跳过，只看会落到界面/卡片上的**字符串字面量**。
    """
    banned = ("永久免费", "永远免费", "永久不收费")

    def literals(path: Path) -> list[tuple[int, str]]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, "body", None) or []
                first = body[0] if body else None
                if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)):
                    docs.add(id(first.value))
        return [(n.lineno, n.value) for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in docs]

    offenders: list[str] = []
    for f in [*PACKAGES.glob("*/src/**/*.py"), *(ROOT / "scripts").glob("*.py")]:
        for lineno, text in literals(f):
            for word in banned:
                if word in text:
                    offenders.append(f"{f.relative_to(ROOT)}:{lineno} {word!r}")
    # 控制台整份都是对外界面：不看上下文，一律不许出现
    console = PACKAGES / "a2n-server" / "src" / "a2n_server" / "web" / "console.html"
    if console.exists():
        for lineno, line in enumerate(console.read_text(encoding="utf-8").splitlines(), 1):
            for word in banned:
                if word in line:
                    offenders.append(f"console.html:{lineno} {word!r}")
    assert not offenders, (
        "对外文案不许承诺「永久免费」（说「不收费」这种当下事实）："
        f"{offenders}"
    )
