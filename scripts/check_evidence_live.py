"""活库核验：试用 / 毕业 / 四段证据 / 计量签名 / 结算收口在**跑起来的系统**里是不是那个样子。

静态测试（tests/test_quality.py）验的是"给定输入算出给定输出"；这里验的是
"端到端装配起来之后，公共 API 吐出来的东西自洽"。两类错不一样：
前者漏了会算错数，后者漏了会**算对了但没人看得到**（端点没挂、投影把它吃掉、
没有一条运行路径真的把签名签出来）。

前置：bash scripts/sim_start.sh fresh（四节点、含 A2N_ROLE=trial 那一档）+ 跑过冒烟
用法：python scripts/check_evidence_live.py
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
TRIAL_NAME = "华南-新秀OCR"      # A2N_ROLE=trial：处于试用期，声明了草稿模板
CHARGING_NAME = "华东-精算OCR"    # A2N_ROLE=charging：已开业（免费期走完、已毕业），模板 v1.0
FREE_NAME = "华北-公益OCR"        # A2N_ROLE=free：**不声明**验收模板

fails: list[str] = []


def get(path: str, principal: str | None = None):
    """运维面（/v1/ops/*）要带 X-Principal —— 缺身份它回 400（单一判据在 router 里）。"""
    req = urllib.request.Request(BASE + path)
    if principal:
        req.add_header("X-Principal", principal)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  ✓ " if cond else "  ✗ ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        fails.append(name)


def main() -> int:
    agents = get("/v1/registry/agents")

    print("① 发现层每行都挂了证据摘要（列表要能一眼看出免费还是收费）")
    missing = [a.get("name") for a in agents if a.get("evidence") is None]
    check("所有 agent 都带 evidence 摘要", not missing, f"缺: {missing}" if missing else "")

    trial = next((a for a in agents if a.get("name") == TRIAL_NAME), None)
    charging = next((a for a in agents if a.get("name") == CHARGING_NAME), None)
    check("找到试用档节点", trial is not None, TRIAL_NAME)
    check("找到收费档节点", charging is not None, CHARGING_NAME)
    if not (trial and charging):
        print("（节点没起齐：确认 sim_start.sh 已拉起 9102 与 9105）")
        return 1

    print("② 试用档：处于试用期、额度 10、尚未用过")
    ev = get(f"/v1/agents/{trial['agent_id']}/evidence")
    t = ev["trial"]
    check("state=TRIAL 且 trial=True", t["state"] == "TRIAL" and t["trial"] is True, json.dumps(t))
    check("首装额度 = 10", t["cap"] == 10, f"cap={t['cap']}")
    check("还没用掉任何额度", t["used"] == 0, f"used={t['used']}")
    check("列表摘要与详情页同源（同一判据）",
          (trial["evidence"]["trial"] or {}).get("trial") is True)

    print("③ 收费档：免费期走完并毕业 ⇒ 表现为收费（卡上没有退出通道）")
    ev2 = get(f"/v1/agents/{charging['agent_id']}/evidence")
    check("trial=False（不再免费）", ev2["trial"]["trial"] is False, json.dumps(ev2["trial"]))
    check("state=GRADUATED", ev2["trial"]["state"] == "GRADUATED")
    check("额度确实走完了（不是靠卡上声明跳过）",
          ev2["trial"]["used"] == ev2["trial"]["cap"] and ev2["trial"]["cap"] == 10,
          f"{ev2['trial']['used']}/{ev2['trial']['cap']}")
    check("额度来源如实记为 INITIAL（首装，不是重连采样）",
          ev2["trial"]["grant_kind"] == "INITIAL", str(ev2["trial"].get("grant_kind")))

    print("④ 四段证据的形状：各自独立、样本不足给原因不给空白")
    o, q, r = ev["objective"], ev["quality"], ev["ratings"]
    check("① 客观表现给口径（平台探测 / 使用端实测 / 计量签名）",
          o.get("rtt_source") == "平台探测" and o.get("observed_source") == "使用端实测"
          and "签名" in (o.get("metering_source") or ""))
    # 这两件事必须分开说：卡上有模板 vs 算过偏差。混在一句里，owner 会去补一个
    # 他早就声明过的东西。
    check("② 已声明模板但**还没有交付样本** ≠ 未声明模板",
          q["declared"] is True and q["measured"] is False and q["quality"] is None
          and "已声明验收模板" in q["note"], q["note"])
    check("③ 使用评价：样本不足 ⇒ score=None 且给出还差几位评分者",
          r["published"] is False and r["score"] is None and r["needed_raters"] == 20,
          json.dumps(r))
    check("④ 案例：新节点还没有公开案例", ev["cases"] == [])
    check("响应里**没有**任何综合总分字段",
          not any(k in ev for k in ("score_total", "overall", "composite", "total_score")))

    print("⑤ 收费档被调用过 ⇒ 偏差指标真的算出来了（模板被满足 ⇒ 质量分 100）")
    evc = get(f"/v1/agents/{charging['agent_id']}/evidence")
    qc = evc["quality"]
    check("measured=True 且三个分量齐全",
          qc["measured"] is True and qc["components"] is not None,
          json.dumps(qc.get("components"), ensure_ascii=False))
    check("模板被满足 ⇒ 质量分 = 100", qc["quality"] == 100.0, f"quality={qc['quality']}")
    check("无参考 ⇒ 内容一致性标为「没测」，而不是伪造 0 偏差",
          qc["no_reference_samples"] >= 1, f"no_ref={qc['no_reference_samples']}")

    print("⑥ 不声明模板的节点：如实说「未声明」，不给一个漂亮的 100 分")
    free = next((a for a in agents if a.get("name") == FREE_NAME), None)
    if free:
        qf = get(f"/v1/agents/{free['agent_id']}/evidence")["quality"]
        check('declared=False 且写明"未声明验收模板"',
              qf["declared"] is False and qf["measured"] is False
              and "未声明验收模板" in qf["note"], qf["note"])
    else:
        check("找到不声明模板的节点", False, FREE_NAME)

    print("⑦ 案例不含调用方身份（没数据也不许漏字段名）")
    cases = evc["cases"]
    leaked = [k for c in cases for k in c if "requester" in k or "principal" in k]
    check("案例里没有调用方身份字段", not leaked, f"漏: {leaked}" if leaked else
          f"（{len(cases)} 条案例）")

    print("⑧ 毕业端点：判据不齐时**说清还差什么**（而不是静默 409）")
    req = urllib.request.Request(
        BASE + f"/v1/agents/{trial['agent_id']}/graduate", method="POST")
    req.add_header("X-Principal", "acct:frank")     # 试用档的 owner
    try:
        urllib.request.urlopen(req, timeout=20)
        check("试用期未满时毕业被拒", False, "居然毕业成功了")
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode("utf-8", "replace"))
        detail = body.get("detail") or body
        blockers = (detail or {}).get("blockers") or []
        check("毕业被拒（409）且带回 blockers 清单",
              e.code == 409 and len(blockers) >= 1,
              " / ".join(blockers[:2]) if blockers else json.dumps(detail)[:120])

    # 这一组守的是**接线**而不是算法：签名算得再对，只要没有一条运行路径真的
    # 把它签出来，活库上就永远是"未签名"——"算对了但没人看得到"是最难发现的一类错。
    print("⑨ 计量签名：真跑过的调用里签出来了（不是一辈子「未签名」）")
    oc = evc["objective"]
    check("收费档有计量样本", oc["metering_samples"] >= 1, f"samples={oc['metering_samples']}")
    check("至少一条是节点真签的（attested ≥ 1）", oc["metering_attested"] >= 1,
          f"attested={oc['metering_attested']}/{oc['metering_samples']}")
    signed = [c for c in cases if c.get("metering_attested")]
    check("已签案例给出的理由是「验签通过」（不是自报即算数）",
          bool(signed) and all(c["metering_reason"] == "验签通过" for c in signed),
          f"{len(signed)}/{len(cases)} 条已签")
    check("逐条案例都带签名结论字段（不是只给一个汇总数）",
          bool(cases) and all("metering_attested" in c for c in cases),
          f"（{len(cases)} 条案例）")
    # 系统性检查：凡是产生过计量的节点都应签得出来。某一个节点一条都没签，
    # 多半就是它那条调用路径漏接了连署 —— 只测一个档位是发现不了的。
    silent: list = []
    for a in agents:
        ob = get(f"/v1/agents/{a['agent_id']}/evidence")["objective"]
        if ob["metering_samples"] > 0 and ob["metering_attested"] == 0:
            silent.append(a.get("name"))
    check("没有「有计量却一条都没签」的节点", not silent,
          f"漏签: {silent}" if silent else f"（{len(agents)} 个节点全签得出来）")

    # ⑩ 结算收口（P1 §3.2）：两条记账路进同一张表、口径不跨币种、运维视图不泄主体。
    # 与 ⑨ 同一类错：`closing.record` 写得再对，只要调用它的那条路没接上，
    # 活库上就永远少一笔 —— "做对了但没人调用"。
    print("⑩ 结算收口：两条记账路进同一张表，且口径不跨币种")
    ops = get("/v1/ops/settlement", principal="acct:ops")
    s = ops["summary"]
    check("总览给出三个数（应结/已结/待处理）",
          all(isinstance(s.get(k), int) for k in ("due_count", "settled_count", "pending_count")),
          json.dumps(s))
    settled = ops["recent_settled"]
    modes = sorted({r["mode"] for r in settled})
    check("已结清单里不止一种记账方式（证明'同一个触发点'真的收口了）",
          len(modes) >= 2, f"方式: {modes}")
    ids = [r["task_id"] for r in settled]
    check("同一次交付只对应一条结算事实（幂等键 = task_id）",
          bool(ids) and len(ids) == len(set(ids)), f"{len(ids)} 行 / {len(set(ids))} 单")
    paid = [r for r in settled if r["mode"] != "free"]
    check("付费的账都带凭据号（能追回分账单/直付回执）",
          bool(paid) and all(r.get("ref") for r in paid), f"{len(paid)} 笔")
    free = [r for r in settled if r["mode"] == "free"]
    check("免费调用也记一笔（金额 0）—— 否则'已结'对不上'验收通过几单'",
          bool(free) and all(r["amount_minor"] == 0 for r in free), f"{len(free)} 笔")
    check("正常路径不留待处理（有就说明某条路的结算真的失败了）",
          s["pending_count"] == 0,
          f"pending={s['pending_count']}")
    cur = s["settled_by_currency"]
    check("金额按币种分行（没有把不同币种加成一个数）",
          bool(cur) and len({r["currency"] for r in cur}) == len(cur),
          " / ".join(f"{r['currency']}×{r['n']}" for r in cur))
    leak = sorted({k for r in settled + ops["recent_pending"]
                   for k in r if k in ("requester_id", "node_id", "principal", "account_id")})
    check("运维聚合视图不泄漏主体（不给调用方/供给方身份）",
          not leak, f"漏: {leak}" if leak else f"（{len(settled)} 笔已结明细）")
    last = ops["last_cut"]
    check("日切在自动跑（起服务即切一次）且对账平",
          last is not None and last.get("balanced") == 1,
          json.dumps(last, ensure_ascii=False) if last else "还没切过账")

    # ⑪ 自源 vs 独立（差值参数）与稳定性采样（重连额度）：
    # 两条都是"隔离但要看得见"——隐藏等于假装没发生，等于另一种撒谎。
    print("⑪ 自源与稳定性采样：隔离但看得见，且不合成结论")
    check("证据里带自源/独立的差值参数块",
          isinstance(ev.get("self_source"), dict)
          and {"self_cases", "independent_cases", "delta_raw", "comparable", "note"}
          <= set(ev["self_source"]), json.dumps(ev.get("self_source"), ensure_ascii=False))
    check("样本不足时**不给差值**（给 None + 说明，不是给个 0）",
          ev["self_source"]["comparable"] is True
          or ev["self_source"]["delta_raw"] is None,
          str(ev["self_source"].get("delta_raw")))
    check("目标表现里报出稳定性采样次数（重连额度，不进质量模板）",
          "stability_calls" in ev["objective"], str(ev["objective"].get("stability_calls")))
    check("质量偏差里报出被排除的稳定性采样数",
          "stability_samples" in ev["quality"], str(ev["quality"].get("stability_samples")))
    check("案例计数说明了排除原因（自源/采样各排掉多少条）",
          {"done_total", "public_basis", "self_excluded", "stability_excluded"}
          <= set(ev.get("case_counts") or {}), json.dumps(ev.get("case_counts")))
    check("列表摘要与详情页的自源块同源",
          (charging["evidence"].get("self_source") or {}).get("self_cases")
          == evc["self_source"]["self_cases"])

    # ⑫ 上架名额（"允许被发现的数量"）：它是**分发策略**不是能力 ——
    # 声明了限额的 agent 带 seats（允许几个 / 此刻占了几个），没声明的明确标"不限"
    # （留空会让人猜"是没设还是满了"）。演示里的免费档声明了 3，这条顺带钉住
    # "供给方填的数字真的落了库、真的到了接口"，而不只是界面自己算出来的。
    print("⑫ 上架名额：声明的数字落了库、到了接口，且两种情形各有明确形状")
    free_agent = next((a for a in agents if a.get("name") == FREE_NAME), None)
    check("免费档带上了声明的名额（3）",
          bool(free_agent) and (free_agent.get("seats") or {}).get("limit") == 3,
          json.dumps((free_agent or {}).get("seats"), ensure_ascii=False)
          if free_agent else f"名单里没找到 {FREE_NAME}（满员就不该出现，但演示档不该满）")
    check("名额给出「允许几个 + 此刻占了几个 + 会不会满」三个判据",
          bool(free_agent) and {"limit", "used", "full", "unlimited"}
          <= set(free_agent.get("seats") or {}),
          json.dumps((free_agent or {}).get("seats"), ensure_ascii=False))
    check("没声明名额的 agent 明确标「不限」（留空会让人猜是没设还是满了）",
          all((a.get("seats") or {}).get("unlimited") is True
              for a in agents if a.get("name") != FREE_NAME),
          json.dumps([(a.get("name"), a.get("seats")) for a in agents
                      if (a.get("seats") or {}).get("unlimited") is not True],
                     ensure_ascii=False))
    if free_agent:
        # 直接点名问一个 agent：不隐藏、如实带出同一份名额事实（不能"问不到"）
        one = get(f"/v1/registry/agents/{free_agent['agent_id']}")
        check("直接点名问得到，且带同一份名额事实（不隐藏）",
              (one.get("seats") or {}).get("limit") == 3,
              json.dumps(one.get("seats"), ensure_ascii=False))

    print("")
    if fails:
        print(f"✗ 活库核验失败 {len(fails)} 项")
        return 1
    print("✓ 活库核验通过：试用/毕业/四段证据/计量签名/结算收口在运行中的系统里自洽")
    return 0


if __name__ == "__main__":
    sys.exit(main())
