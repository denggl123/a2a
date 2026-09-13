"""节点的家：**库路径必须在 import 任何 a2n 包之前定好**。

为什么单独一个模块、且不 import 任何 a2n 包：`a2n-store` 在 import 时读环境变量
A2N_DB 并在模块级缓存连接（`_local` 线程局部）。也就是说，库路径一旦 import 就定死，
之后再改环境变量是无效的 —— 而"悄悄连到别人的库"是最难查的一类事故：
数据看着都对，只是写进了错误的文件。

所以顺序必须是：

    from a2n_node.home import use_home      # ← 零依赖，先跑
    home = use_home("data/nodes/alice")
    from a2n_node import SovereignNode      # ← 这时才 import a2n-store

本模块刻意保持零 a2n 依赖（只有标准库），这样它在 import 顺序上永远排得进第一位。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 一个节点的家里有：身份钥匙、卡片、自己的库
IDENTITY_FILE = "identity.json"
CARD_FILE = "card.json"
DB_FILE = "a2n.db"


def same_path(a: str | Path, b: str | Path) -> bool:
    """两个路径是不是同一个。

    必须过 `resolve()`：Windows 上同一个目录可能有两种写法（8.3 短名
    `ADMINI~1` 与长名 `Administrator`），`abspath` 不做这个归一化 ——
    于是"其实是一个库"会被判成"两个库"，节点根本起不来。
    """
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return os.path.abspath(str(a)) == os.path.abspath(str(b))


def use_home(home: str | Path, *, force: bool = False) -> Path:
    """把这个进程的库指向 home/a2n.db，并建好目录。返回 home 路径。

    默认（force=False）：**a2n-store 已经 import 过**（库已锁死）且本次要指去别处，
    就直接报错 —— "一个节点 = 一个进程 = 一个库"，跨库跑不是配置问题，是设计错误。

    判断"改不动了"的依据是 `a2n_store` 是否已在 sys.modules，**不是**环境变量
    是否已存在：环境变量会被子进程继承（父进程为自己的节点定过库，子进程一起来
    就带着 A2N_DB），而子进程此后调用 use_home 时还没 import 过 a2n-store，
    它是这个进程的第一决定权。拿继承来的值去否决它，会让"再起一个进程跑第二个
    节点"这条最基本的用法失效。
    """
    h = Path(home).expanduser().resolve()
    h.mkdir(parents=True, exist_ok=True)
    want = str(h / DB_FILE)
    cur = os.environ.get("A2N_DB")
    if cur and not same_path(cur, want) and not force and _store_loaded():
        raise RuntimeError(
            f"这个进程的库已经指向 {cur}，不能再改到 {want}。\n"
            f"一个节点 = 一个进程 = 一个库：要跑第二个节点就再起一个进程。"
        )
    os.environ["A2N_DB"] = want
    return h


def _store_loaded() -> bool:
    """a2n-store 是否已经 import（它一 import 就读环境变量并缓存连接）。"""
    return "a2n_store" in sys.modules or "a2n_store.db" in sys.modules


def db_path_of(home: str | Path) -> str:
    return str(Path(home).expanduser().resolve() / DB_FILE)


def identity_path(home: str | Path) -> Path:
    return Path(home).expanduser().resolve() / IDENTITY_FILE


def card_path(home: str | Path) -> Path:
    return Path(home).expanduser().resolve() / CARD_FILE


def read_json(path: str | Path) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def write_json(path: str | Path, obj) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
