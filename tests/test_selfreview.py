# -*- coding: utf-8 -*-
"""2026-10-15 自我审查轮的回归测试 —— 不联网、纯合成数据。

    python -m tests.test_selfreview

每一条对应自查里发现并修掉的一个问题。
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import os
import shutil
import sqlite3
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

from asxquant import archive as AR                # noqa: E402
from asxquant import forward as FW                # noqa: E402
from asxquant import validate as V                # noqa: E402
from asxquant.config import all_stock_tickers     # noqa: E402
from tests.test_step1 import _synthetic_px        # noqa: E402

OK, FAIL = [], []
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print("  %s %s%s" % ("[PASS]" if cond else "[FAIL]", name,
                         ("  -- " + detail) if detail else ""))


def _tmp_archive():
    tmp = tempfile.mkdtemp()
    old = AR._db_path
    AR._db_path = lambda: os.path.join(tmp, "a.db")
    return tmp, old


def test_keep_first_record():
    print("\n[S1] 前瞻记录以第一次为准：开盘后的重跑不能把准时记录改成「迟到」")
    tmp, old = _tmp_archive()
    try:
        rep = {"as_of": "2026-08-28", "sectors": [], "money_flow": {},
               "recommendation": {"short": {"sector_name": "x", "picks": [{"code": "ARF"}]},
                                  "long": {"sector_name": "y", "picks": [{"code": "CSL"}]}},
               "benchmark": {}, "direction": {}, "forecasts": []}
        AR.record(rep, log=lambda m: None)
        con = sqlite3.connect(os.path.join(tmp, "a.db"))
        first = con.execute("SELECT recorded_at FROM market_snapshots").fetchone()[0]
        con.execute("UPDATE market_snapshots SET recorded_at='2026-08-28T20:00:00+00:00'")
        con.commit()
        con.close()
        rep["recommendation"]["short"]["picks"] = [{"code": "ZZZ"}]
        AR.record(rep, log=lambda m: None)                      # 同一 asof 的晚到重跑
        con = sqlite3.connect(os.path.join(tmp, "a.db"))
        rec, picks = con.execute("SELECT recorded_at, short_picks FROM market_snapshots").fetchone()
        con.close()
        check("重跑不覆盖首次记录时刻", rec == "2026-08-28T20:00:00+00:00", rec)
        check("重跑不覆盖首次记录的名单", picks == "ARF", picks)
        check("首次记录时刻带时区", first.endswith("+00:00"))
        n1 = AR.record_scores("2026-10-15", [{"ticker": "CSL.AX", "short_score": 1.0}])
        n2 = AR.record_scores("2026-10-15", [{"ticker": "CSL.AX", "short_score": -5.0}])
        con = sqlite3.connect(os.path.join(tmp, "a.db"))
        v = con.execute("SELECT short_score FROM score_snapshots").fetchone()[0]
        con.close()
        check("全横截面评分也以第一次为准", v == 1.0 and n1 == n2 == 1)
    finally:
        AR._db_path = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_restore_tool_timezones():
    print("\n[S2] 修复工具：没有时区的旧记录按提交时区解读，两种读法都更早才恢复")
    spec = importlib.util.spec_from_file_location("rfr", os.path.join(ROOT, "tools", "restore_first_records.py"))
    rfr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rfr)
    t = rfr._instant("2026-08-27T20:17:51", "2026-08-27T20:20:11+10:00")
    check("本地提交的记录按悉尼时间读", t.astimezone(dt.timezone.utc).hour == 10)
    t = rfr._instant("2026-08-28T20:19:34", "2026-08-28T20:19:35Z")
    check("云端提交的记录按 UTC 读", t.utcoffset() == dt.timedelta(0))
    both = rfr._both_readings("2026-08-31T16:18:54")
    check("现在这条无时区：给出 UTC、悉尼两种读法", len(both) == 2)
    first = rfr._instant("2026-08-27T20:17:51", "2026-08-27T20:20:11+10:00")
    check("时区有歧义时不恢复（首次只比一种读法早）",
          not all(first < c for c in rfr._both_readings("2026-08-27T19:14:00")))


def test_forward_pool_and_multiplicity():
    print("\n[S3] 前瞻成绩单：对照组用与回测相同的流动性门槛；三个持有期一起看要做多重检验修正")
    rng = np.random.default_rng(3)
    idx = pd.bdate_range("2026-08-03", periods=80)
    cols = ["S%02d.AX" % i for i in range(30)]
    c = pd.DataFrame(np.cumprod(1 + rng.normal(0, 0.01, (80, 30)), axis=0) * 10, index=idx, columns=cols)
    vol = pd.DataFrame(1e6, index=idx, columns=cols)
    vol["S29.AX"] = 10.0                                    # 成交额远低于 300 万
    c["S29.AX"] = c["S29.AX"] * np.r_[np.ones(11), np.full(69, 5.0)]   # 进场后暴涨 5 倍
    px = {"close": c, "adjclose": c, "volume": vol}
    rec = [{"asof": str(idx[10].date()), "picks": ["S01.AX"], "recorded_at": str(idx[10].date()) + "T20:00:00"}]
    row = FW.score_records(px, rec, cols, 5, "short")[0]
    r = c.iloc[16] / c.iloc[11] - 1
    exp_pool = r.drop("S29.AX").mean()
    check("不够流动的股票不进对照组（否则一只暴涨的小票就能扭曲全池）",
          abs(row["pool"] - round(exp_pool, 5)) < 1e-9, "%s vs %.5f" % (row["pool"], exp_pool))
    many = [{"asof": str(idx[i].date()), "picks": ["S05.AX"],
             "recorded_at": str(idx[i].date()) + "T12:00:00"} for i in range(0, 70)]
    s = FW.summarize(FW.score_records(px, many, cols, 5, "short"), 5, "short")
    check("Bonferroni：p_adj = min(1, 3p)", s["p"] is None or abs(s["p_adj"] - min(1.0, round(s["p"] * 3, 3))) < 0.002,
          "%s / %s" % (s["p"], s["p_adj"]))
    check("主判据是看空 20 日", FW.summarize([], 20, "short")["primary"] and
          not FW.summarize([], 5, "short")["primary"])


def test_verdict_by_p_and_basket_test():
    print("\n[S4] 判决按 p 值；「篮子跑输市场」这个差距本身也要检验")
    check("p=0.06 判「微弱」，不是「勉强达标」", V._verdict_p(0.06) == "weak")
    check("p=0.04 判「勉强达标」", V._verdict_p(0.04) == "marginal")
    rng = np.random.default_rng(2)
    idx = pd.bdate_range("2022-01-03", periods=300)
    s = pd.Series(rng.normal(0.02, 0.1, 300), index=idx)
    st = V._overlap_stats(s, 20)                            # 不重叠只有 15 个点，自由度 14
    check("小样本时判决跟着 p 走", st["verdict"] == V._verdict_p(st["p_value"]))
    px = _synthetic_px(n=700, seed=8)
    tks = [t for t in all_stock_tickers() if t in px["close"].columns][:30]
    sp = pd.DataFrame(np.abs(rng.normal(3, 1.2, (len(px["close"]), len(tks)))),
                      index=px["close"].index, columns=tks)
    v = V.run_validation(px, sp, tks, {t: "materials" for t in tks}, horizon=20, n_side=10)
    svm = v.get("short_vs_market") or {}
    check("报告里有空头篮子相对市场的检验（t、p、段数）",
          svm.get("n", 0) >= 10 and svm.get("t") is not None and 0 <= svm.get("p", -1) <= 1, str(svm))


def main():
    print("=" * 68)
    print("澳股雷达 2026-10-15 自我审查轮回归测试")
    print("=" * 68)
    for fn in (test_keep_first_record, test_restore_tool_timezones,
               test_forward_pool_and_multiplicity, test_verdict_by_p_and_basket_test):
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
