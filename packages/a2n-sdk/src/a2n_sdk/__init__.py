"""A2N SDK —— 给 Agent 与程序调用用（机器对机器）。

管理台是给人看的，SDK 是给机器用的：
  client      注册、心跳（带连接自报）、长轮询取任务、提交结果与计量
  shelf       货架：像上架商品一样把 agent 摆上 A2N（shelf/from_card/update_card）
  runner      起一个常驻节点，自动执行任务；全程只出站，家宽零配置
  connection  本机连通性自检（本机 IP、STUN 反射候选，NAT 判定交给平台）
  transport   反向长连接（隧道）+ 中继转发 + 本地服务挂载
  console     内嵌本地管理台（127.0.0.1:8770），只看本机，数据不出你的电脑

命令行（codex / claude code 等 shell 型 agent 直接跑）：
  python -m a2n_sdk shelf --help
"""
from .client import Client
from .connection import connection_report, local_ips, stun_reflexive
from .console import LocalConsole
from .errors import A2NError, CallDeniedError, PaymentRequiredError
from .runner import Node, run_forever
from .shelf import auto_desc, build_card, from_card, gen_name, gen_uid, shelf, update_card
from .transport import TunnelClient, serve_local_agent

__all__ = ["Client", "Node", "run_forever", "LocalConsole", "connection_report",
           "local_ips", "stun_reflexive", "TunnelClient", "serve_local_agent",
           "shelf", "from_card", "update_card", "build_card", "gen_uid", "gen_name",
           "auto_desc", "A2NError", "PaymentRequiredError", "CallDeniedError"]
