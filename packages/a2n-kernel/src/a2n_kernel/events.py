"""领域事件总线：跨模块默认异步通信。

**内核不许碰存储**（否则依赖方向就倒过来了）。所以这里只做内存分发，
事件落库由装配层通过 `set_sink()` 注入 —— 通常是 a2n_store.outbox.append。

这样 a2n-kernel 保持零依赖：它能被任何包引用，而它不引用任何包。

## 事务语义（重要）

调用方背着未提交的业务写入时，事件与业务**同事务提交** —— 所以正确的
调用顺序是"先 publish 再 commit"（倒过来等于业务提交与事件落库之间留着一个
崩溃丢事件的窗口）。

但**订阅者的通知不许在事务里执行**：订阅者多半自己要写库（开争议单、
退款、扣声誉），它会 commit —— 把发布方的事务提前提交，原子性当场失效。
所以：publish 时如果背着事务，通知只进本线程的延迟队列，等提交之后由
装配层注入的钩子（store.on_commit(flush_deferred)）再依次送达；
事务回滚则丢弃队列（没提交的事实，不该有通知）。

判定"是否背着事务"需要知道存储状态，内核看不见存储 —— 由装配层注入
`set_in_tx_probe()`。未注入时退化为"就地通知"（内核单测等场景的旧行为）。
"""
from __future__ import annotations

import threading
from typing import Any, Callable

_subscribers: dict[str, list[Callable[[dict], None]]] = {}
_sink: Callable[[str, dict], None] | None = None
_in_tx: Callable[[], bool] | None = None
_deferred_local = threading.local()      # 延迟通知按线程隔离（连接也是线程本地的）


def subscribe(event_type: str, handler: Callable[[dict], None]) -> None:
    _subscribers.setdefault(event_type, []).append(handler)


def set_sink(fn: Callable[[str, dict], None] | None) -> None:
    """注入事件落库实现（Outbox 与业务同事务由 sink 自己保证）。"""
    global _sink
    _sink = fn


def set_in_tx_probe(fn: Callable[[], bool] | None) -> None:
    """注入"当前是否背着未提交事务"的判定（装配层：lambda: conn().in_transaction）。"""
    global _in_tx
    _in_tx = fn


def _deferred() -> list[tuple[str, dict]]:
    q = getattr(_deferred_local, "q", None)
    if q is None:
        q = []
        _deferred_local.q = q
    return q


def publish(event_type: str, payload: dict[str, Any]) -> None:
    """发布事件：先落库（可审计），再通知订阅者。

    背着事务时：落库加入当前事务（随业务原子提交），**通知进入延迟队列**
    （提交后送达；回滚则丢弃）。没有事务时：落库自提交、通知就地送达。
    """
    in_tx = bool(_in_tx and _in_tx())        # 必须在 sink 落库之前判定：
    if _sink:                                # sink 自己的 INSERT 也会打开事务
        try:
            _sink(event_type, payload)
        except Exception:  # noqa: BLE001 - 落库失败不得中断业务主流程
            pass
    if in_tx:
        _deferred().append((event_type, dict(payload)))
        return
    _notify(event_type, payload)


def _notify(event_type: str, payload: dict[str, Any]) -> None:
    for handler in _subscribers.get(event_type, []):
        try:
            handler(payload)
        except Exception:  # noqa: BLE001 - 公证层挂掉只影响"章还没刻"，不拖垮业务
            pass
    for handler in _subscribers.get("*", []):
        try:
            handler({"type": event_type, **payload})
        except Exception:  # noqa: BLE001
            pass


def flush_deferred() -> None:
    """提交后触发：把背在事务里的事件通知依次送达（由 store 的提交钩子调用）。"""
    q = _deferred()
    while q:
        event_type, payload = q.pop(0)
        _notify(event_type, payload)


def discard_deferred() -> None:
    """回滚后触发：没提交的事件，通知也不许发（由 store 的回滚钩子调用）。"""
    _deferred().clear()
