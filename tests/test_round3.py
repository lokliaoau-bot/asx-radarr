# -*- coding: utf-8 -*-
"""2026-10-15 第三轮（吸收 ChatGPT 反馈 + 前瞻成绩单）的回归测试 —— 不联网、纯合成数据。

    python -m tests.test_round3
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

from asxquant import archive as AR                # noqa: E402
from asxquant import forward as FW                # noqa: E402
from asxquant import validate as V                # noqa: E402

OK, FAIL = [], []
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print("  %s %s%s" % ("[PASS]" if cond else "[FAIL]", name,
                         ("  -- " + detail) if detail else ""))


# ----------------------------------------------------------------------
def test_inference():
    print("\n[1] 判决统计量：固定起点的不重叠 t，按日历取样")
    rng = np.random.default_rng(1)
    idx = pd.bdate_range("2022-01-03", periods=600)
    s = pd.Series(rng.normal(-0.02, 0.1, 600), index=idx)
    st = V._overlap_stats(s, 20)
    w = s.iloc[0::20]
    manual = w.mean() / (w.std(ddof=1) / np.sqrt(len(w)))
    check("t_stat 就是第 0 个起点、每 20 天取一个的 t", abs(st["t_stat"] - round(manual, 2)) < 1e-9,
          "%s vs %.2f" % (st["t_stat"], manual))
    check("p 值来自 t 分布且在 0-1 之间", st["p_value"] is not None and 0 <= st["p_value"] <= 1)
    check("Newey-West 只作诊断保留", "t_nw" in st and st["inference"] == "fixed_phase0_nonoverlap")
    check("判决跟着不重叠 t 走", st["verdict"] == V._verdict(manual))
    gap = s.copy()
    gap.iloc[5:9] = np.nan                                  # 中间缺几天
    st2 = V._overlap_stats(gap, 20)
    check("缺失日不会让取样点错位（缺的不是第 0 相位的点，t 不变）",
          st2["t_stat"] == st["t_stat"], "%s vs %s" % (st2["t_stat"], st["t_stat"]))
    check("全部起点的方向一致性被列出", st["phases"] == 20 and 0 <= st["phases_same_sign"] <= 20)
    ic = V._ic(pd.DataFrame(rng.normal(size=(30, 25)), index=idx[:30]),
               pd.DataFrame(rng.normal(size=(30, 25)), index=idx[:30]), min_names=20)
    check("_ic 保留日历槽（长度等于日期数，不 dropna）", len(ic) == 30)


# ----------------------------------------------------------------------
def _px(n=80, names=30, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2026-08-03", periods=n)
    cols = ["S%02d.AX" % i for i in range(names)]
    c = pd.DataFrame(np.cumprod(1 + rng.normal(0, 0.01, (n, names)), axis=0) * 10, index=idx, columns=cols)
    return {"close": c, "adjclose": c.copy(), "open": c.copy(), "high": c * 1.01, "low": c * 0.99,
            "volume": pd.DataFrame(1e6, index=idx, columns=cols)}


def test_forward():
    print("\n[2] 前瞻成绩单：迟到不计分、未满期留着、停牌单独计、口径与回测一致")
    px = _px()
    idx = px["close"].index
    uni = list(px["close"].columns)
    d0 = idx[10]
    early = (dt.datetime.combine(d0.date(), dt.time(20, 0))).isoformat()        # 信号日晚上（UTC）
    late = (dt.datetime.combine(idx[11].date(), dt.time(5, 0))).isoformat()     # 次日 05:00 UTC = 悉尼 15/16 点
    recs = [{"asof": str(d0.date()), "picks": ["S01.AX", "S02.AX"], "recorded_at": early},
            {"asof": str(idx[12].date()), "picks": ["S01.AX"], "recorded_at": late.replace(str(idx[11].date()), str(idx[13].date()))},
            {"asof": str(idx[-3].date()), "picks": ["S03.AX"], "recorded_at": str(idx[-3].date()) + "T19:00:00"}]
    rows = FW.score_records(px, recs, uni, 5, "short")
    st = {r["asof"]: r["status"] for r in rows}
    check("信号日当晚记录的算前瞻、已满期", st[str(d0.date())] == "matured")
    check("开盘之后才记录的标「迟到」", st[str(idx[12].date())] == "late")
    check("还没满 h 天的标「未满期」", st[str(idx[-3].date())] == "pending")
    A = px["adjclose"]
    r1 = A.iloc[16] / A.iloc[11] - 1
    exp = r1[["S01.AX", "S02.AX"]].mean() - r1.mean()
    got = [r for r in rows if r["status"] == "matured"][0]["excess"]
    check("超额 = 名单平均 - 全池平均（t+1 收盘进、再 h 天收盘出）", abs(got - round(exp, 5)) < 1e-9,
          "%s vs %.5f" % (got, exp))
    px2 = _px()
    px2["volume"].iloc[11, 1] = 0                      # S01 进场日停牌
    rows2 = FW.score_records(px2, recs[:1], uni, 5, "short")
    check("进场日停牌的名字不计入、单独计数（不当 0 收益）",
          rows2[0]["picks_used"] == 1 and rows2[0]["picks_untradable"] == 1)
    many = [{"asof": str(idx[i].date()), "picks": ["S05.AX"],
             "recorded_at": str(idx[i].date()) + "T12:00:00"} for i in range(0, 60)]
    s = FW.summarize(FW.score_records(px, many, uni, 5, "short"), 5, "short")
    check("不重叠段：持有期互不重叠（60 天 / 每段 6 天 ≈ 9-10 段）", 8 <= s["nonoverlap_n"] <= 10,
          str(s["nonoverlap_n"]))
    check("少于 10 段一律「还太早」", s["verdict"] == "too_early" or s["nonoverlap_n"] >= 10)


# ----------------------------------------------------------------------
def test_archive_tables():
    print("\n[3] 存档：全横截面评分、看空雷达排名；记录时刻带时区")
    tmp = tempfile.mkdtemp()
    old = AR._db_path
    AR._db_path = lambda: os.path.join(tmp, "a.db")
    try:
        n = AR.record_scores("2026-10-15", [
            {"ticker": "CSL.AX", "short_score": 0.5, "long_score": -0.2, "liquid_short": True, "liquid_long": False},
            {"ticker": "BHP.AX", "short_score": None, "long_score": None}])
        check("评分为空的股票不写", n == 1)
        radar10 = {"calendars": {"AU": {"expected_session": "2026-10-15"}}, "rows": [
            {"symbol_id": "AU.CSL", "technical_eligible": True, "known_contribution": 10.0, "session_date": "2026-10-15"},
            {"symbol_id": "AU.BHP", "technical_eligible": True, "known_contribution": 30.0, "session_date": "2026-10-15"},
            {"symbol_id": "AU.CTD", "technical_eligible": False, "known_contribution": None, "session_date": "2026-10-15"},
            {"symbol_id": "AU.QAN", "technical_eligible": True, "known_contribution": 50.0, "session_date": "2026-10-14"}]}
        r = AR.record_shortradar(radar10)
        con = sqlite3.connect(os.path.join(tmp, "a.db"))
        got = con.execute("SELECT code, rank, eligible_n, recorded_at FROM shortradar_snapshots ORDER BY rank").fetchall()
        con.close()
        check("只存可排名、且价格日期等于期望交易日的", [g[0] for g in got] == ["BHP", "CSL"], str(got))
        check("排名按分数从高到低", got[0][1] == 1 and got[0][2] == 2)
        check("记录时刻是带时区的 UTC", got[0][3].endswith("+00:00"))
    finally:
        AR._db_path = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_merge_new_tables():
    print("\n[4] 存档合并：一边有新表、另一边没有，合并后新表的行不丢")
    tmp = tempfile.mkdtemp()
    try:
        a, b = os.path.join(tmp, "ours.db"), os.path.join(tmp, "theirs.db")
        for p, rows in ((a, 3), (b, 1)):
            con = sqlite3.connect(p)
            con.execute("CREATE TABLE market_snapshots (asof TEXT PRIMARY KEY, x REAL)")
            con.executemany("INSERT INTO market_snapshots VALUES (?,?)",
                            [("2026-10-%02d" % (i + 1), i) for i in range(rows)])
            con.commit()
            con.close()
        con = sqlite3.connect(b)                         # 只有 theirs 有新表
        con.execute("CREATE TABLE score_snapshots (asof TEXT, code TEXT, PRIMARY KEY (asof, code))")
        con.execute("INSERT INTO score_snapshots VALUES ('2026-10-15','CSL')")
        con.commit()
        con.close()
        p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "merge_archive.py"), b + ".base", a, b],
                           capture_output=True, text=True)
        con = sqlite3.connect(a)
        n_new = con.execute("SELECT count(*) FROM score_snapshots").fetchone()[0]
        n_old = con.execute("SELECT count(*) FROM market_snapshots").fetchone()[0]
        con.close()
        check("合并驱动退出 0", p.returncode == 0, p.stderr[-200:])
        check("对方新表的行保留下来", n_new == 1)
        check("双方旧表并集", n_old == 3)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------
def test_eval_grouping():
    print("\n[5] 实测脚本分组：只按当天评分分组，未来缺价不改变分组（H-EVAL-01）")
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import eval_short_radar as E
    rng = np.random.default_rng(7)
    idx = pd.bdate_range("2024-01-01", periods=10)
    S = pd.DataFrame(rng.normal(size=(10, 100)), index=idx)
    F = pd.DataFrame(rng.normal(size=(10, 100)), index=idx)
    full, members, unresolved = E.quintiles(S, F, 5)
    top = S.iloc[0].rank(method="first") > 80
    F2 = F.copy()
    F2.iloc[0, np.where(top.values)[0][:10]] = np.nan   # 最高组里 10 只未来缺价
    q2, members2, unresolved2 = E.quintiles(S, F2, 5)
    check("成员总数不因未来缺价而减少", members2 == members == 200)
    check("缺价的单独计数", unresolved2 == unresolved + 10)
    check("其余组不受影响（最低组结果一致）", abs(q2[0][0] - full[0][0] + (F2.iloc[0].mean() - F.iloc[0].mean())) < 1e-9)


def test_no_archive_write_on_synthetic():
    print("\n[6] 测试用的合成数据绝不写进真实存档")
    from asxquant import publish as PB
    called = []
    old = AR.record_shortradar
    AR.record_shortradar = lambda *a, **k: called.append(1)
    tmp = tempfile.mkdtemp()
    try:
        snap = {"radar": {"10": {"calendars": {"AU": {"expected_session": "2026-10-15"}}, "rows": []}},
                "import": {}}
        PB.build_short(tmp, snap=snap, log=lambda m: None)
        check("传入合成 snap 时不调用存档记录", called == [])
    finally:
        AR.record_shortradar = old
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("=" * 68)
    print("澳股雷达 2026-10-15 第三轮回归测试")
    print("=" * 68)
    for fn in (test_inference, test_forward, test_archive_tables, test_merge_new_tables,
               test_eval_grouping, test_no_archive_write_on_synthetic):
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
