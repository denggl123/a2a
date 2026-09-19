"""演示库播种：把"已开业"的演示节点补成**毕业态**。

## 为什么需要这个脚本

**卡上不许声明退出免费期**：任何想被发现的 agent，前 10 次完成的调用一律不计费
（`a2n_registry.trial` 的 docstring 写了为什么 —— 目的在使用方：谁都能先拿真实
案例看看这东西是不是自己要的）。

所以演示里那三个"已经开业、按价目表收费"的档位（专业版·收费 / 公益版·免费 /
极速版·x402）**不能再靠卡上写一行 `x-a2n.trial=false` 一上来就可收费**了 ——
它们必须真的走完免费期。

真实路径是：做满 10 次**完成**的调用 → 再过四道毕业闸门（额度用尽 / 上架参数齐 /
公开证据够 / 没有未了结争议）。演示没必要真跑 30 次网络调用，所以这里**如实伪造
那个前提**：按名字找到 agent，把额度按"完成调用"消耗掉，再置为毕业。

三条纪律，缺一条都别用它：

1. **只调 a2n-registry 自己的 API**（`trial.consume` / `trial.graduate`）——
   不直写 trial_offers，表归属不破；
2. **它是一个命令行播种脚本，不是任何 HTTP 路由** —— 产品面上没有这条后门，
   外部拿不到；能跑它的人本来就能直接改库；
3. **它只播种"已开业"档位**，第四档（入门版）保持试用态 ——
   控制台的"试用中 N/10 · 免费"徽标要靠它才有真身。
   `main()` 也接命令行名字，`sim_start.sh` 用它再把市场里三个收费档补成毕业态。

幂等：已毕业的跳过。用法：`sim_start.sh` 在四节点起来后调一次。
"""
from __future__ import annotations

import sys

from a2n_registry import trial
from a2n_store import conn, init_db

# 与 scripts/run_a2a_node.py 的 PRESETS 里**非 trial 档**的展示名对应。
# 只认名字，不认 agent_id（每次冷启 id 都会变）。
ESTABLISHED = ("OCR 识别 · 专业版", "OCR 识别 · 公益版", "OCR 识别 · 极速版")


def seed(names: tuple[str, ...]) -> int:
    init_db()
    marks = ",".join("?" * len(names))
    rows = conn().execute(
        f"SELECT agent_id, name FROM agents WHERE name IN ({marks}) ORDER BY name",
        names).fetchall()
    if not rows:
        print(f"[seed] 没找到任何已开业档位（找的是 {list(names)}）——"
              f"确认 sim_start.sh 已经把节点拉起来")
        return 1
    for r in rows:
        aid, name = r["agent_id"], r["name"]
        st = trial.state(aid)
        if st is None:
            print(f"[seed] {name}：没有试用记录，跳过")
            continue
        if st["state"] == trial.GRADUATED:
            print(f"[seed] {name}：已毕业，跳过")
            continue
        # 走真实的额度消耗（consume 只由"完成"的路径调用，这里就是那 10 次完成）
        before = int(st["used"])
        while trial.in_trial(aid):
            trial.consume(aid)
        trial.graduate(aid)
        after = trial.state(aid) or {}
        print(f"[seed] {name}：免费期走完 {int(after.get('used') or 0)}/"
              f"{int(after.get('cap') or 0)} 次（原 {before}）→ 已毕业，此后按价目表收费")
    return 0


def main() -> int:
    names = tuple(sys.argv[1:]) or ESTABLISHED
    return seed(names)


if __name__ == "__main__":
    raise SystemExit(main())
