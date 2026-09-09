"""A2N HTTP 接口层。"""
from __future__ import annotations

from fastapi import FastAPI, Header
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from a2n_store import init_db
from .routers import (a2a, ap2, arbitration, consensus, custodian, deals, ledger,
                      payments, public, registry, tasks, transport, wallet)
from . import wiring

app = FastAPI(title="A2N", version="0.2.0", description="只发行情、只刻章、不碰钱的 Agent 服务网络")

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
app.include_router(ledger.router)     # 账户与流水：只读聚合视图 + 媒介清单

WEB_DIR = Path(__file__).resolve().parent / "web"


@app.on_event("startup")
def _startup() -> None:
    init_db()
    # 装配层接线：所有模块间"谁连谁"，统一在 wiring 里，只写一次
    wiring.wire()


@app.get("/console", include_in_schema=False)
def console() -> FileResponse:
    return FileResponse(WEB_DIR / "console.html")


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/health")
def health(principal: str | None = Header(default=None, alias="X-Principal")) -> dict:
    return {"ok": True, "principal": principal}
