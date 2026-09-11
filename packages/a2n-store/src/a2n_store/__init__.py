"""a2n-store（L0 存储）：唯一的建表与连接出口。

约定：每张表只属于一个包，但建表集中在这里 —— 因为 SQLite 单库、事务必须同库。
生产目标 Postgres 时，这里换成连接池 + 迁移脚本，业务代码不动。

tx() 是给"读-判-写"用的显式写事务：余额、额度、状态推进这类地方必须包它，
否则两个并发请求可以同时看到同一份旧值然后一起写穿。
"""
from . import outbox
from .db import SCHEMA, TRIGGERS, conn, init_db, on_commit, on_rollback, tx

__all__ = ["SCHEMA", "TRIGGERS", "conn", "init_db", "outbox", "tx",
           "on_commit", "on_rollback"]
