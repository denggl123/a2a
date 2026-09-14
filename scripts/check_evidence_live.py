"""活库核验：试用 / 毕业 / 四段证据 / 计量签名在**跑起来的系统**里是不是那个样子。

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
CHARGING_NAME = "华东-精算OCR"    # A2N_ROLE=charging：退出试用（已毕业），模板 v1.0
FREE_NAME = "华北-公益OCR"        # A2N_ROLE=free：**不声明**验收模板

fails: list[str] = []


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
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

    print("③ 收费档：刻意退出试用 ⇒ 表现为已毕业")
    ev2 = get(f"/v1/agents/{charging['agent_id']}/evidence")
    check("trial=False（不再免费）", ev2["trial"]["trial"] is False, json.dumps(ev2["trial"]))
    check("state=GRADUATED", ev2["trial"]["state"] == "GRADUATED")

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

    print("")
    if fails:
        print(f"✗ 活库核验失败 {len(fails)} 项")
        return 1
    print("✓ 活库核验通过：试用/毕业/四段证据在运行中的系统里自洽")
    return 0


if __name__ == "__main__":
    sys.exit(main())
