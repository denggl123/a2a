"""a2n-server（L5 装配）：把各域包拼成一个可运行的服务。

这里只做三件事：路由装配、身份透传（X-Principal）、静态管理台。
业务逻辑一行都不许写 —— 写了就说明某个包缺了 Port。
"""
from .app import app

__all__ = ["app"]
