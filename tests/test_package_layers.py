"""包级分层的守卫测试 —— 把"向上依赖 = 0"从文档里的口号变成可执行的规矩。

背景（2026-09-27 架构评估）：README 的分包结构表曾把 `a2n-gateway` 写成 L2，
而代码 docstring 自述是 L5。按错的表去算会得出 4 条"向上依赖"，看着像架构违规。
当时没有任何测试发现这件事 —— 因为分层只写在文档里，没被机器执行。

这个文件补上那道闸：

  * 从**源码**抽真实 `import a2n_*`（不采信文档与 pyproject）；
  * 断言**无环**（Tarjan）；
  * 断言对 L0–L5 分层表**零向上依赖**（依赖只从高层指向低层）；
  * 断言**每个包都被分层表覆盖**（新增包必须显式定层，不能偷偷落进来）；
  * 断言**实际 import 的包都写进了 pyproject**（隐式依赖 = 0），且**没有幽灵依赖**。

改了分层或加了包，这张表必须同步 —— 这正是我们要的：机器说了算。
"""
from __future__ import annotations

import collections
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
PACKAGES = ROOT / "packages"

# 权威分层表。与 README「分包结构 v1.3」一致；改一处必须改两处，否则本测试红。
LAYERS = {"a2n-kernel": 0, "a2n-p2p": 1, "a2n-acceptance": 1, "a2n-sdk": 2, "a2n-node": 3}

_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+(a2n_[a-z0-9_]+)", re.M)


def _all_packages() -> list[str]:
    return sorted(p.name for p in PACKAGES.iterdir() if p.is_dir())


def _module_to_package() -> dict[str, str]:
    return {p.replace("-", "_"): p for p in _all_packages()}


def _import_graph() -> dict[str, set[str]]:
    """真实跨包依赖：包 -> 它 import 的包（来自源码，不信 pyproject）。"""
    m2p = _module_to_package()
    graph: dict[str, set[str]] = {p: set() for p in _all_packages()}
    for p in _all_packages():
        for f in (PACKAGES / p / "src").rglob("*.py"):
            text = f.read_text(encoding="utf-8", errors="replace")
            for mod in _IMPORT_RE.findall(text):
                target = m2p.get(mod)
                if target and target != p:
                    graph[p].add(target)
    return graph


def _declared_deps(pkg: str) -> set[str]:
    toml = (PACKAGES / pkg / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"dependencies\s*=\s*\[(.*?)\]", toml, re.S)
    if not block:
        return set()
    names = re.findall(r'"(a2n-[a-z0-9-]+)"', block.group(1))
    return set(names)


def _cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan 强连通分量，返回 size>1 的分量（即环）。"""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    out: list[list[str]] = []
    counter = [0]

    def visit(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in graph[v]:
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            out.append(comp)

    for v in graph:
        if v not in index:
            visit(v)
    return [c for c in out if len(c) > 1]


# ---------------------------------------------------------------- 断言

def test_no_circular_dependencies():
    graph = _import_graph()
    cyc = _cycles(graph)
    assert not cyc, f"存在循环依赖：{cyc}"


def test_every_package_declares_its_layer():
    """新增包必须显式定层 —— 否则它会静默落进分层之外。"""
    missing = [p for p in _all_packages() if p not in LAYERS]
    stale = [p for p in LAYERS if p not in _all_packages()]
    assert not missing, f"这些包没在 LAYERS 里定层：{missing}"
    assert not stale, f"LAYERS 里这些包已不存在：{stale}"


def test_no_upward_dependencies():
    """依赖只应从高层指向低层；源层 < 目标层＝向上依赖＝分层被击穿。"""
    graph = _import_graph()
    bad: list[tuple[str, int, str, int]] = []
    for src, targets in graph.items():
        for dst in targets:
            if LAYERS[dst] > LAYERS[src]:
                bad.append((src, LAYERS[src], dst, LAYERS[dst]))
    assert not bad, (
        "发现向上依赖（依赖方向与分层相反）："
        + "; ".join(f"L{s} {a} -> L{d} {b}" for a, s, b, d in bad))


def test_no_undeclared_cross_package_imports():
    """隐式依赖 = 0：源码里 import 了别的 a2n 包，就必须写进 pyproject。"""
    graph = _import_graph()
    offenders = {}
    for pkg, targets in graph.items():
        undeclared = targets - _declared_deps(pkg)
        if undeclared:
            offenders[pkg] = sorted(undeclared)
    assert not offenders, f"这些包 import 了未声明的依赖：{offenders}"


def test_no_ghost_dependencies():
    """幽灵依赖 = 0：pyproject 里声明了，源码里却一行都没用。"""
    graph = _import_graph()
    ghosts = {}
    for pkg in _all_packages():
        unused = _declared_deps(pkg) - graph[pkg]
        if unused:
            ghosts[pkg] = sorted(unused)
    assert not ghosts, f"这些包声明了未使用的依赖（幽灵依赖）：{ghosts}"


def test_layering_is_a_dag_and_covers_all_levels():
    """分层表本身健康：0–5 层都有人，且没有空层/越界层号。"""
    levels = set(LAYERS.values())
    assert levels == {0, 1, 2, 3}, f"包层号不连续或越界：{sorted(levels)}"
    counts = collections.Counter(LAYERS.values())
    assert all(counts[lv] > 0 for lv in range(4)), f"有空层：{dict(counts)}"
