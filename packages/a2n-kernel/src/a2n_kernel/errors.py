"""领域异常：业务层抛 A2NError 子类，路由层统一转 HTTP 状态码。

约定（全库执行）：
  - service 层只抛异常，不做 HTTP；
  - 路由层 `except A2NError as e: raise HTTPException(e.http_status, str(e))`，
    或者沿用旧的 `except ValueError`（A2NError 是 ValueError 子类，零改动兼容）。

为什么不用裸 ValueError：400/404/409 的区分不该靠解析错误文案。
为什么放在内核：异常是跨包契约，且内核不认识任何业务概念 ——
它只知道"出错了"这个通用形状。
"""
from __future__ import annotations


class A2NError(ValueError):
    """领域错误基类。code 给机器判别，message 给人读。"""

    code = "a2n_error"
    http_status = 400


class ValidationError(A2NError):
    """输入形状不合法（缺字段、格式错、越界）。→ 400"""

    code = "validation"


class NotFoundError(A2NError):
    """资源不存在。→ 404"""

    code = "not_found"
    http_status = 404


class ConflictError(A2NError):
    """状态冲突：唯一标识已被占用、不可重复操作、身份不可变。→ 409"""

    code = "conflict"
    http_status = 409
