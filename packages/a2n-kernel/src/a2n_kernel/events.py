"""领域事件总线：跨模块默认异步通信。

**内核不许碰存储**（否则依赖方向就倒过来了）。所以这里只做内存分发，
事件落库由装配层通过 `set_sink()` 注入 —— 通常是 a2n_store.outbox.append。

这样 a2n-kernel 保持零依赖：它能被任何包引用，而它不引用任何包。
"""
from __future__ import annotations

from typing import Any, Callable

_subscribers: dict[str, list[Callable[[dict], None]]] = {}
_sink: Callable[[str, dict], None] | None = None


def subscribe(event_type: str, handler: Callable[[dict], None]) -> None:
    _subscribers.setdefault(event_type, []).append(handler)


def set_sink(fn: Callable[[str, dict], None] | None) -> None:
    """注入事件落库实现（Outbox 与业务同事务由 sink 自己保证）。"""
    global _sink
    _sink = fn


def publish(event_type: str, payload: dict[str, Any]) -> None:
    """发布事件：先落库（可审计），再通知订阅者。

    事务语义：调用方背着未提交的业务写入时，事件与业务**同事务提交** ——
    所以正确的调用顺序是"先 publish 再 commit"，别再倒过来
    （倒过来等于业务提交与事件落库之间留着一个崩溃丢事件的窗口）。
    订阅者与业务共用连接，此刻读到的就是同一事务里的数据。

    订阅者抛异常一律吞掉 —— 公证层挂掉只影响"章还没刻"，不该拖垮业务，
    这是刻意的失败语义。
    """
    if _sink:
        try:
            _sink(event_type, payload)
        except Exception:  # noqa: BLE001 - 落库失败不得中断业务主流程
            pass
    for handler in _subscribers.get(event_type, []):
        try:
            handler(payload)
        except Exception:  # noqa: BLE001
            pass
    for handler in _subscribers.get("*", []):
        try:
            handler({"type": event_type, **payload})
        except Exception:  # noqa: BLE001
            pass
