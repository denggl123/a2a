"""A2N HTTP 接口层。"""
from __future__ import annotations

import asyncio
import os
import re

from fastapi import FastAPI, Header, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from a2n_kernel.errors import A2NError
from a2n_settlement import closing
from a2n_store import init_db
from .routers import (a2a, ap2, arbitration, consensus, custodian, deals, invoke,
                      ledger, ops, payments, public, quality, registry, tasks,
                      transport, wallet)
from . import wiring

app = FastAPI(title="A2N", version="0.2.0", description="只发行情、只刻章、不碰钱的 Agent 服务网络")


@app.exception_handler(A2NError)
def _a2n_error_handler(request: Request, exc: A2NError) -> JSONResponse:
    """领域异常兜底：路由层忘了转 HTTP 时，状态码也不许将就。

    409 的关键语义（冲突/被占用）不能被降格成 400 —— 调用方要能靠状态码
    做机器判别，不是解析错误文案。
    """
    return JSONResponse(status_code=exc.http_status,
                        content={"error": str(exc), "code": exc.code})


@app.exception_handler(Exception)
def _internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """未预期异常统一成 JSON 500：默认的纯文本 500 会把前端 JSON 解析打崩，
    连"哪一步炸了"都展示不出来。原文不回显（可能含内部细节），只给类型名。"""
    return JSONResponse(status_code=500,
                        content={"error": "内部错误", "type": type(exc).__name__})


app.include_router(registry.router)
app.include_router(tasks.router)
app.include_router(transport.router)
app.include_router(custodian.router)
app.include_router(wallet.router)
app.include_router(public.router)
app.include_router(deals.router)
app.include_router(consensus.router)
app.include_router(arbitration.router)
app.include_router(ap2.router)
app.include_router(a2a.router)        # A2A v1.0 协议入口：/a2a/{agent_id} (JSON-RPC) + Agent Card
app.include_router(a2a.root_router)   # 域名级 /.well-known/agent.json?agent_id=
app.include_router(payments.router)   # 直付：支付方式登记 + 成交合约核验
app.include_router(invoke.router)     # 程序化调用：与 A2A 同一条治理链（门禁→任务→验收→记账）
app.include_router(ledger.router)     # 账户与流水：只读聚合视图 + 媒介清单
app.include_router(quality.router)    # 质量证据：评价 / 模板偏差 / 试用与毕业（四段证据）
app.include_router(ops.router)        # 运维：结算与对账（应结/已结/待处理 + 日切 + 报警）

WEB_DIR = Path(__file__).resolve().parent / "web"
# 控制台"我是谁"的注入锚点：只认 <body ... data-principal="..."> 这一处属性。
# 两个分组：① 前缀（到值的开引号为止）② 收尾的引号；被替换的是中间那段值。
# **整条属性只命中一次**（页面只有一个 body），换完就走。
_PRINCIPAL_ATTR = re.compile(r'(<body[^>]*\sdata-principal=")[^"]*(")')


@app.on_event("startup")
def _startup() -> None:
    init_db()
    # 装配层接线：所有模块间"谁连谁"，统一在 wiring 里，只写一次
    wiring.wire()


def _closing_interval() -> float:
    """日切节拍（秒）。默认 **0 = 关**。

    默认关是有意的：测试与脚本化调用不该被后台任务打扰 —— 定时对账是
    "运维的一件事"，不该成为一个永远开着的副作用。演示/生产用
    `A2N_CLOSING_INTERVAL_SEC` 打开（scripts/sim_start.sh 已开）。
    """
    try:
        return float(os.environ.get("A2N_CLOSING_INTERVAL_SEC") or 0)
    except ValueError:
        return 0.0


@app.on_event("startup")
async def _start_closing_loop() -> None:
    """结算与对账的定时节拍：**验收通过即自动结算**（在事务内），
    日切对账只是按节拍再核一次「积分 ≡ 托管」并把差异写进运维页。"""
    iv = _closing_interval()
    if iv <= 0:
        return

    async def _loop() -> None:
        while True:
            try:
                # 先切一次再去睡：运维页一进来就该有真数据，不是"等一会儿才有"
                await run_in_threadpool(closing.daily_cut)
            except Exception:      # noqa: BLE001 - 对账失败不能把服务带崩
                pass
            await asyncio.sleep(iv)

    asyncio.create_task(_loop())


@app.get("/console", include_in_schema=False)
def console() -> HTMLResponse:
    """控制台页面。"开箱是谁"由**部署**决定，不由页面写死。

    控制台是"某个节点操作者"看的界面，它的默认身份在生产里来自登录/本地配置 ——
    所以这里读 `A2N_CONSOLE_PRINCIPAL`，把它替换进 `<body data-principal>`。
    演示环境由 `scripts/sim_start.sh` 注入**本机节点的 did**（先 `--print-did`
    取出来再起平台）：打开控制台看到的就是"我这台机器上架了什么"。

    未配置时保留页面上那个中性兜底值 —— 单源仍在**服务端发出的这份 HTML** 里，
    前端读法不变（`document.body.dataset.principal`），不新增第二处真相。
    """
    html = (WEB_DIR / "console.html").read_text(encoding="utf-8")
    who = os.environ.get("A2N_CONSOLE_PRINCIPAL")
    if who:
        html, n = _PRINCIPAL_ATTR.subn(rf'\g<1>{who}\g<2>', html, count=1)
        if not n:
            # 页面里没有这个锚点 = 前端读法变了，注入会静默失效（控制台开箱成兜底身份）
            # 这是我们自己的配置跟页面对不上，不是调用方的错 → 走 500。
            raise RuntimeError(
                "console.html 里找不到 data-principal 锚点，A2N_CONSOLE_PRINCIPAL 注入无效")
    return HTMLResponse(html)


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/health")
def health(principal: str | None = Header(default=None, alias="X-Principal")) -> dict:
    return {"ok": True, "principal": principal}
