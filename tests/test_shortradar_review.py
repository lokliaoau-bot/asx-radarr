# -*- coding: utf-8 -*-
"""ChatGPT 看空雷达（/short/）2026-10-15 代码审查的回归测试 —— 不联网、纯合成数据。

    python -m tests.test_shortradar_review
"""
from __future__ import annotations

import copy
import datetime as dt
import sys

import numpy as np

sys.path.insert(0, ".")

import tests.test_shortradar as T                  # noqa: E402
from asxquant import shortradar as SR              # noqa: E402

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print("  %s %s%s" % ("[PASS]" if cond else "[FAIL]", name,
                         ("  -- " + detail) if detail else ""))


def _run(end, now, px=None):
    idx = T.asx_days(end=end or dt.date(2026, 9, 30))
    px = px or T.frames(["CSL.AX", "BHP.AX", "GMG.AX"], idx, seed=2)
    return SR.build(px, T.frames([SR.BENCH], idx, seed=9), now=now, log=lambda m: None,
                    px_fetched_at=now - dt.timedelta(hours=1), extra_fetched_at=now - dt.timedelta(hours=1))


def test_calendar_time_bomb():
    print("\n[R1] 日历定时炸弹：2026 年平安夜之后不能整页失效；日历快用完要提前报警")
    now = dt.datetime(2027, 1, 15, 10, 0, tzinfo=dt.timezone.utc)
    snap = _run(dt.date(2027, 1, 15), now)
    rows = snap["radar"]["10"]["rows"]
    check("快进到 2027-01-15：股票照常排名（官方补充说明生效）",
          all(r["technical_eligible"] for r in rows), str([r["state"] for r in rows]))
    check("四份补充说明都通过 ChatGPT 原有的严格校验", len(snap["import"]["calendar_overrides"]["applied"]) == 4,
          str(snap["import"]["calendar_overrides"]))
    early = SR.build(T.frames(["CSL.AX"], T.asx_days(end=dt.date(2026, 9, 30)), seed=2), None,
                     now=dt.datetime(2026, 9, 30, 10, 0, tzinfo=dt.timezone.utc), log=lambda m: None,
                     px_fetched_at=dt.datetime(2026, 9, 30, 9, 0, tzinfo=dt.timezone.utc))
    check("核对时刻之前的运行不使用它们（不用「未来才有」的信息）",
          early["import"]["calendar_overrides"]["applied"] == [])
    check("现在往后 120 天没有日历缺口", SR.calendar_readiness(dt.date(2026, 10, 15))["issues"] == [])
    iss = SR.calendar_readiness(dt.date(2027, 10, 1))["issues"]
    check("2027-10-01 时提前 92 天报警「2028 年日历还没录入」",
          len(iss) == 1 and iss[0]["kind"] == "calendar_year_not_reviewed" and iss[0]["date"] == "2028-01-01")


def test_short_halts():
    print("\n[R2] 短暂停牌照常计算；长期停牌、当天停牌照旧挡下")
    idx = T.asx_days()
    px = T.frames(["CSL.AX", "BHP.AX", "GMG.AX", "QAN.AX"], idx, seed=4)

    def halt(t, start, n):
        start = start % len(idx)                     # 负数下标换成正数，idx[-1:0] 会是空切片
        days = idx[start:start + n]
        for k in ("close", "open", "high", "low"):
            px[k].loc[days, t] = px["close"][t].iloc[start - 1]
        px["volume"].loc[days, t] = 0.0
    halt("CSL.AX", -100, 2)          # 停牌两天
    halt("BHP.AX", -150, 8)          # 连续停牌 8 天
    halt("GMG.AX", -1, 1)            # 今天停牌
    snap = SR.build(px, T.frames([SR.BENCH], idx, seed=9), now=T.NOW, log=lambda m: None,
                    px_fetched_at=T.FETCHED, extra_fetched_at=T.FETCHED)
    rows = {r["symbol_id"]: r for r in snap["radar"]["10"]["rows"]}
    check("停牌两天：照常排名，并写明原因", rows["AU.CSL"]["technical_eligible"]
          and "short_halts_inside_history" in rows["AU.CSL"]["reasons"], rows["AU.CSL"]["state"])
    check("连续停牌 8 天：不排名", not rows["AU.BHP"]["technical_eligible"])
    check("今天停牌：不排名", not rows["AU.GMG"]["technical_eligible"] and rows["AU.GMG"]["state"] == "no_trades",
          rows["AU.GMG"]["state"])
    check("没停牌的不受影响", rows["AU.QAN"]["technical_eligible"])
    from short_radar.contracts import REASON_LABELS
    check("新原因有大白话说明（页面不会显示「还需检查」）", "short_halts_inside_history" in REASON_LABELS)


def test_page_contract_check():
    print("\n[R3] 构建时先按手机页面的规则自查：正常数据零违规，被改坏的数据能被抓到")
    snap = _run(None, T.NOW)
    check("正常数据零违规", all(v == [] for v in snap["import"]["page_contract_violations"].values()),
          str(snap["import"]["page_contract_violations"]))
    bad = copy.deepcopy(snap["radar"]["10"])
    bad["rows"][0]["known_contribution"] = (bad["rows"][0]["known_contribution"] or 0) + 3
    bad["rows"][1]["factors"]["valuation"] = 0.5
    rules = {v["rule"] for v in SR.check_snapshot(bad, 10)}
    check("分数和权重对不上被抓到", "known_contribution != weights x factors" in rules, str(rules))
    check("没接入的因子出现数值被抓到", "unintegrated factors must be null" in rules)
    bad2 = copy.deepcopy(snap["radar"]["10"])
    bad2["coverage"]["AU"]["technical_eligible"] += 1
    check("覆盖数和行数对不上被抓到", any(v["rule"] == "coverage counts differ from rows"
                                       for v in SR.check_snapshot(bad2, 10)))


def test_dead_code_removed():
    print("\n[R4] 用不到的联网/服务器模块已从 App 里移除（原件留在 ai-short-radar 目录）")
    import importlib.util
    gone = [m for m in ("__main__", "server", "providers", "eodhd", "evaluation", "demo")
            if importlib.util.find_spec("short_radar." + m) is None]
    check("6 个模块都不在了", len(gone) == 6, str(gone))


def main():
    print("=" * 68)
    print("ChatGPT 看空雷达代码审查回归测试")
    print("=" * 68)
    for fn in (test_calendar_time_bomb, test_short_halts, test_page_contract_check, test_dead_code_removed):
        try:
            fn()
        except Exception as e:
            import traceback
            FAIL.append(fn.__name__)
            print("  [ERROR] %s: %s" % (fn.__name__, e))
            traceback.print_exc()
    print("\n" + "=" * 68)
    print("通过 %d 项，失败 %d 项" % (len(OK), len(FAIL)))
    if FAIL:
        print("失败项: " + ", ".join(FAIL))
    print("=" * 68)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
