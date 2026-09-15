"""运维：结算与对账（P1 §3.2；控制台 §5.2 的「结算与对账」页）。

这一页只回答三个问题：

  1. 今天的账**该结多少 / 结了多少 / 还有多少没结**（三个数，口径写清）；
  2. 对账**平不平**（积分总量 ≡ 托管余额），不平差在哪；
  3. 没结上的**是哪几单、为什么**（能点开追溯到具体任务）。

两条纪律落在这里：

  · **金额不跨币种相加**：三个头条数字是笔数（无币种），金额按币种分行。
    分与 10⁻⁶ 相加得到的数没有任何解释力。
  · **不泄漏主体**：待处理清单给 task_id / 方式 / 金额 / 原因，**不给**
    requester_id 与 node_id —— 逐笔明细仍走按行为相关方鉴权的
    `/v1/console/calls/{id}`，运维聚合视图不是枚举主体的旁路。

身份判据只放一处：`X-Principal` 声明成**可选**头 + 显式 `if not principal: 400`。
若声明成必填头，缺头时框架会先返回 422，那句 400 就成了永远走不到的死代码 ——
"没身份"到底回哪个码，必须只有一个答案。
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from a2n_settlement import closing

router = APIRouter(prefix="/v1/ops", tags=["ops"])

# 运维面字段白名单：主体标识一律不出（见模块 docstring 第 2 条）。
# `ref`（pc__… / dl__… 凭据号）**要留**：它是不透明凭据，不指向任何主体，
# 而它正是"这一笔能追回分账单/直付回执"的唯一线索。白名单的本意是挡
# requester_id / node_id，不是把所有字段都挡掉 —— 挡过头会让凭据列永远只有"—"。
_OPS_ROW_FIELDS = ("task_id", "mode", "state", "amount_minor", "currency", "ref",
                   "reason", "attempts", "created_at", "updated_at")


def _sanitize(rows: list[dict]) -> list[dict]:
    return [{k: r.get(k) for k in _OPS_ROW_FIELDS} for r in rows]


@router.get("/settlement")
def settlement_overview(day: str | None = None,
                        principal: str | None = Header(default=None, alias="X-Principal")) -> dict:
    """结算总览：三个数 + 币种分行 + 报警位 + 最近一次日切。"""
    if not principal:
        raise HTTPException(400, "缺少 X-Principal")
    return {
        "summary": closing.today_summary(day),
        "alert": closing.alert_state(),
        "last_cut": closing.latest(),
        "history": closing.history(14),          # 日切历史（近两周）：结论与差异留痕
        "recent_settled": _sanitize(closing.recent(20)),
        "recent_pending": _sanitize(closing.pending(20)),
    }


@router.get("/settlement/pending")
def settlement_pending(limit: int = 50,
                       principal: str | None = Header(default=None, alias="X-Principal")) -> list[dict]:
    """待处理清单：**没结上的是哪几单、为什么**。绝不静默。"""
    if not principal:
        raise HTTPException(400, "缺少 X-Principal")
    return _sanitize(closing.pending(limit))


@router.get("/settlement/settled")
def settlement_settled(limit: int = 50,
                       principal: str | None = Header(default=None, alias="X-Principal")) -> list[dict]:
    if not principal:
        raise HTTPException(400, "缺少 X-Principal")
    return _sanitize(closing.recent(limit))


@router.get("/closing/history")
def closing_history(limit: int = 30,
                    principal: str | None = Header(default=None, alias="X-Principal")) -> list[dict]:
    """日切历史：只存结论与差异，明细账仍在流水/托管账簿里。"""
    if not principal:
        raise HTTPException(400, "缺少 X-Principal")
    return closing.history(limit)


@router.post("/closing/run")
def closing_run(day: str | None = None,
                principal: str | None = Header(default=None, alias="X-Principal")) -> dict:
    """立即日切（对账 + 汇总 + 落库 + 报警）。

    可重复跑：同日是**覆盖**而不是追加 —— 追加会把"今天跑了几次"
    变成一条假历史（见 closing.daily_cut）。
    """
    if not principal:
        raise HTTPException(400, "缺少 X-Principal")
    return closing.daily_cut(day)
