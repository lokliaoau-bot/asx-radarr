# -*- coding: utf-8 -*-
"""自我记录 / 自我复盘 / 改进提案（journal.py）的回归测试 —— 不联网、纯合成数据。

    python -m tests.test_journal
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sqlite3
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

from asxquant import archive as AR                # noqa: E402
from asxquant import journal as JR                # noqa: E402

OK, FAIL = [], []
NOW = dt.datetime(2026, 10, 15, 9, 0, tzinfo=dt.timezone.utc)


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print("  %s %s%s" % ("[PASS]" if cond else "[FAIL]", name,
                         ("  -- " + detail) if detail else ""))


def _tmp():
    tmp = tempfile.mkdtemp()
    old = AR._db_path
    AR._db_path = lambda: os.path.join(tmp, "a.db")
    return tmp, old


def _done(tmp, old):
    AR._db_path = old
    shutil.rmtree(tmp, ignore_errors=True)


def _px(n=120, k=30, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2026-03-02", periods=n)
    cols = ["S%02d.AX" % i for i in range(k)]
    c = pd.DataFrame(np.cumprod(1 + rng.normal(0, 0.005, (n, k)), axis=0) * 10, index=idx, columns=cols)
    return {"close": c, "adjclose": c.copy(), "volume": pd.DataFrame(1e6, index=idx, columns=cols)}


def test_episodes_and_tags():
    print("\n[J1] 大失误按独立事件计数；三类可能原因都能识别")
    px = _px()
    A, idx = px["adjclose"], px["close"].index
    # S01：第 31 天单日暴涨 20%（之后一直保持）；连续 6 天都在看空名单上 -> 一个事件
    A.loc[idx[31]:, "S01.AX"] *= 1.20
    # S02：持有期间空头占比从 8% 降到 6%（回补），价格稳步上涨 10%
    A.loc[idx[41]:idx[61], "S02.AX"] *= np.linspace(1.0, 1.10, len(A.loc[idx[41]:idx[61]]))
    A.loc[idx[62]:, "S02.AX"] *= 1.10
    spct = pd.DataFrame(5.0, index=idx, columns=A.columns)
    spct.loc[idx[50]:, "S02.AX"] = 6.0
    spct.loc[:idx[49], "S02.AX"] = 8.0
    # S03 与 S04 同板块，整个板块一起涨 15%
    for t in ("S03.AX", "S04.AX"):
        A.loc[idx[71]:idx[90], t] *= np.linspace(1.0, 1.15, len(A.loc[idx[71]:idx[90]]))
        A.loc[idx[91]:, t] *= 1.15
    px["close"] = A
    sector_of = {t: "x" for t in A.columns}
    sector_of["S03.AX"] = sector_of["S04.AX"] = "hot"
    recs = [{"asof": str(idx[i].date()), "picks": ["S01.AX"], "recorded_at": str(idx[i].date()) + "T08:00:00+00:00"}
            for i in range(25, 31)]
    recs += [{"asof": str(idx[40].date()), "picks": ["S02.AX"], "recorded_at": str(idx[40].date()) + "T08:00:00+00:00"},
             {"asof": str(idx[70].date()), "picks": ["S03.AX"], "recorded_at": str(idx[70].date()) + "T08:00:00+00:00"}]
    m = JR.review_misses(px, spct, sector_of, recs, 20)
    by = {e["code"]: e for e in m["recent_episodes"]}
    # 第 6 天（asof=idx[30]）要在 idx[31] 收盘才买入，那时暴涨已经发生 -> 对它不是失误，所以是 5 天
    check("S01 连续上榜的 5 次失误只算 1 个事件（第 6 天买入时已暴涨完，不算失误）",
          by.get("S01", {}).get("days") == 5 and m["miss_episodes"] == 3,
          str(m["recent_episodes"]))
    check("S01 标「单日暴涨」", "jump" in by.get("S01", {}).get("tags", []))
    check("S02 标「空头在回补」", "covering" in by.get("S02", {}).get("tags", []))
    check("S03 标「整个板块一起涨」", "sector" in by.get("S03", {}).get("tags", []))
    check("「规律」按独立事件算占比", m["pattern"]["jump"]["count"] == 1 and m["pattern"]["jump"]["share"] == round(1 / 3, 3))


def test_proposal_rules():
    print("\n[J2] 提案规则：独立事件 ≥5 且同一原因占 ≥60% 才写；数据停更写提案")
    base = {"pattern": {"jump": {"count": 3, "share": 0.75}}, "miss_episodes": 4}
    check("4 个独立事件（哪怕 75% 同一原因）不写提案", JR.make_proposals(base, {}, {}, {}) == [])
    five = {"pattern": {"jump": {"count": 3, "share": 0.6}}, "miss_episodes": 5}
    p = JR.make_proposals(five, {}, {}, {})
    check("5 个、60% 写提案，且写明怎么检验", len(p) == 1 and "EVAL_END" in p[0]["test"], str(p))
    many_rows = {"pattern": {"jump": {"count": 20, "share": 0.9}}, "big_misses": 30, "miss_episodes": 2}
    check("只次很多但独立事件只有 2 个：不写（不被重复计数骗）", JR.make_proposals(many_rows, {}, {}, {}) == [])
    st = JR.make_proposals({}, {}, {}, {"asic_stale": True, "asic_lag_trading_days": 12})
    check("ASIC 停更写提案", [x["key"] for x in st] == ["asic-stale"])


def test_proposal_lifecycle():
    print("\n[J3] 提案生命周期：条件消失自动标「已解决」，再出现重新打开，从不删除")
    tmp, old = _tmp()
    try:
        con = AR._connect()
        stale = JR.make_proposals({}, {}, {}, {"asic_stale": True, "asic_lag_trading_days": 12})
        open1 = JR.upsert_proposals(con, stale, NOW)
        open2 = JR.upsert_proposals(con, [], NOW + dt.timedelta(days=1))
        st = con.execute("SELECT status FROM proposals WHERE key='asic-stale'").fetchone()[0]
        check("出现时是 open", [p["key"] for p in open1] == ["asic-stale"])
        check("条件消失后标 resolved、不删", open2 == [] and st == "resolved")
        open3 = JR.upsert_proposals(con, stale, NOW + dt.timedelta(days=2))
        check("再出现重新打开", [p["key"] for p in open3] == ["asic-stale"])
        check("始终只有一行", con.execute("SELECT count(*) FROM proposals").fetchone()[0] == 1)
        con.close()
    finally:
        _done(tmp, old)


def test_health_and_guard():
    print("\n[J4] 数据健康：ASIC 晚 4 天正常，晚超过 8 个交易日判过期")
    idx = pd.bdate_range("2026-09-01", "2026-10-15")
    check("晚 4 个交易日", JR.asic_lag(idx, "2026-10-09", "2026-10-15") == 4)
    check("晚 12 个交易日", JR.asic_lag(idx, "2026-09-29", "2026-10-15") == 12)
    px = {"close": pd.DataFrame(index=idx)}
    h = JR.data_health(px, {"as_of": "2026-10-15", "short_as_of": "2026-09-29"}, NOW)
    check("过期被标出", h["asic_stale"] is True)
    h = JR.data_health(px, {"as_of": "2026-10-15", "short_as_of": "2026-10-09"}, NOW)
    check("正常不误报", h["asic_stale"] is False and h["price_stale"] is False)


def test_run_log_and_reflect():
    print("\n[J5] 自我记录：每次运行记代码版本；复盘写入存档、同一天以最后一次为准")
    tmp, old = _tmp()
    os.environ["GITHUB_SHA"] = "abc1234def"
    try:
        con = AR._connect()
        px = _px()
        rep = {"as_of": str(px["close"].index[-1].date()), "short_as_of": str(px["close"].index[-5].date()),
               "generated_at": "2026-10-15 20:00:00", "calibration": {"trust": {"p_right": 0.8}, "state": {}}}
        out = JR.reflect(px, None, rep, {t: "x" for t in px["close"].columns}, con=con, now=NOW)
        row = json.loads(con.execute("SELECT json FROM run_log").fetchone()[0])
        check("运行日志记下云端提交号", row["code"]["commit"] == "abc1234def" and row["code"]["where"] == "github-actions")
        check("复盘有大白话结论", len(out["notes"]) >= 3, str(out["notes"][:2]))
        JR.reflect(px, None, rep, {t: "x" for t in px["close"].columns}, con=con, now=NOW + dt.timedelta(hours=1))
        md = JR.to_markdown(out, rep["calibration"])
        check("给编码助手的复盘摘要：有今天的复盘、提案段落和代码版本",
              "## 今天的复盘" in md and "改进提案" in md and "abc1234def" in md)
        check("同一天复盘只保留一份（可重算的派生结果）",
              con.execute("SELECT count(*) FROM reflection_log").fetchone()[0] == 1)
        check("运行日志每次一行（两次运行两行）", con.execute("SELECT count(*) FROM run_log").fetchone()[0] == 2)
        n1 = AR.record_components("2026-10-15", [{"ticker": "S01.AX", "liquid_short": True, "short_parts": {"mom_neg": 0.1}}], con)
        AR.record_components("2026-10-15", [{"ticker": "S01.AX", "liquid_short": True, "short_parts": {"mom_neg": 9.9}}], con)
        v = json.loads(con.execute("SELECT json FROM component_snapshots").fetchone()[0])["S01"]["mom_neg"]
        check("成分快照以第一次为准", n1 == 1 and v == 0.1)
        con.close()
    finally:
        os.environ.pop("GITHUB_SHA", None)
        _done(tmp, old)


def main():
    print("=" * 68)
    print("自我记录 / 自我复盘 / 改进提案 回归测试")
    print("=" * 68)
    for fn in (test_episodes_and_tags, test_proposal_rules, test_proposal_lifecycle,
               test_health_and_guard, test_run_log_and_reflect):
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
