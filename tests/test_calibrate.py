# -*- coding: utf-8 -*-
"""自动校准（calibrate.py）的回归测试 —— 不联网、纯合成数据。

    python -m tests.test_calibrate

用合成数据故意造出几种情况，检查预先登记的规则只在该动的时候动。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

from asxquant import archive as AR                # noqa: E402
from asxquant import calibrate as CAL             # noqa: E402

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print("  %s %s%s" % ("[PASS]" if cond else "[FAIL]", name,
                         ("  -- " + detail) if detail else ""))


def _px(n=420, k=40, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-01-01", periods=n)
    cols = ["S%02d.AX" % i for i in range(k)]
    c = pd.DataFrame(np.cumprod(1 + rng.normal(0, 0.015, (n, k)), axis=0) * 10, index=idx, columns=cols)
    return {"close": c, "adjclose": c, "volume": pd.DataFrame(1e6, index=idx, columns=cols)}


def _archive(px, champion, challengers=None, days=None, late=False, seed=2):
    """把合成评分写进临时存档。champion/challenger: 'noise' | 'good' | 'wrong'。"""
    rng = np.random.default_rng(seed)
    tmp = tempfile.mkdtemp()
    old = AR._db_path
    AR._db_path = lambda: os.path.join(tmp, "a.db")
    con = AR._connect()
    A, idx = px["adjclose"], px["close"].index
    fut = A.shift(-21) / A.shift(-1) - 1                     # 与校准同口径的未来 20 日收益

    def make(kind, i):
        f = fut.iloc[i]
        noise = rng.normal(size=len(f))
        if kind == "good":
            return -f.rank().values + 3 * noise              # 分越高、之后越弱 = 看空得准
        if kind == "wrong":
            return f.rank().values + 3 * noise
        return noise
    for i in (days or range(25, len(idx) - 25)):
        d = idx[i]
        # 准时 = 信号日 08:00 UTC（悉尼傍晚）；迟到 = 下一个交易日 06:00 UTC（悉尼下午，已开盘）。
        # 不能写成「信号日 + 38 小时」：周五的信号 38 小时后还是周日，并没有晚于周一开盘。
        when = (idx[i + 1] + pd.Timedelta(hours=6)) if late else (d + pd.Timedelta(hours=8))
        rec = when.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        asof = str(d.date())
        for t, v in zip(A.columns, make(champion, i)):
            con.execute("INSERT INTO score_snapshots VALUES (?,?,?,?,?,?,?)",
                        (asof, t[:-3], 0.0, float(v), 1, 1, rec))
        for m, kind in (challengers or {}).items():
            for t, v in zip(A.columns, make(kind, i)):
                if m == "technical_v1":
                    con.execute("INSERT INTO shortradar_snapshots VALUES (?,?,?,?,?,?)",
                                (asof, t[:-3], float(v), 1, 40, rec))
                else:
                    con.execute("INSERT INTO challenger_snapshots VALUES (?,?,?,?,?)",
                                (asof, m, t[:-3], float(v), rec))
    con.commit()
    return tmp, old, con


def _done(tmp, old, con):
    con.close()
    AR._db_path = old
    shutil.rmtree(tmp, ignore_errors=True)


BT = {"mean_ic": -0.03, "t_stat": -1.9}


def test_switch():
    print("\n[C1] 对手明显更准、自己也有效 → 自动换将；状态写进 model_decisions")
    px = _px()
    tmp, old, con = _archive(px, "noise", {"short_build": "good", "neg_mom20": "noise"})
    try:
        out = CAL.run(px, BT, con=con)
        check("段数够 10 段", out["trust"]["prospective_segments"] >= 10, str(out["trust"]["prospective_segments"]))
        check("换成了更准的那个对手", out["state"]["active_model"] == "short_build", str(out["state"]))
        check("状态改变被记录", con.execute("SELECT count(*) FROM model_decisions").fetchone()[0] == 1)
        out2 = CAL.run(px, BT, con=con)
        check("再跑一次不会重复记录、也不会换回去",
              out2["actions_today"] == [] and out2["state"]["active_model"] == "short_build")
    finally:
        _done(tmp, old, con)


def test_pause():
    print("\n[C2] 系统看空分实盘方向反了 → 自动暂停")
    px = _px(seed=3)
    tmp, old, con = _archive(px, "wrong", {"short_build": "noise", "neg_mom20": "noise"})
    try:
        out = CAL.run(px, BT, con=con)
        check("暂停", out["state"]["paused"] is True, str(out["evidence"]["champion"]))
        check("把握标签变成「方向可能是反的」", out["trust"]["label"] == "方向可能是反的", str(out["trust"]))
    finally:
        _done(tmp, old, con)


def test_too_early_and_late():
    print("\n[C3] 证据不足不动作；迟到的记录不计入")
    px = _px(seed=4)
    tmp, old, con = _archive(px, "wrong", {"short_build": "good"}, days=range(25, 120))
    try:
        out = CAL.run(px, BT, con=con)
        check("不到 10 段：不暂停、不换将", out["state"] == {"active_model": "champion", "paused": False}
              and out["actions_today"] == [], str(out["trust"]["prospective_segments"]))
        check("把握照样按实盘更新（起点是回测打五折）", out["trust"]["p_right"] is not None)
    finally:
        _done(tmp, old, con)
    tmp, old, con = _archive(px, "wrong", {"short_build": "good"}, late=True)
    try:
        out = CAL.run(px, BT, con=con)
        check("全部迟到：一段都不算", out["trust"]["prospective_segments"] == 0 and out["actions_today"] == [])
    finally:
        _done(tmp, old, con)


def test_technical_v1_not_switchable():
    print("\n[C4] ChatGPT 看空雷达分只参加评比，不会被换上场（预先规定）")
    px = _px(seed=5)
    tmp, old, con = _archive(px, "noise", {"technical_v1": "good"})
    try:
        out = CAL.run(px, BT, con=con)
        e = out["evidence"]["challengers"].get("technical_v1", {})
        check("它确实更准（被评比到了）", e.get("diff_t") is not None and e["diff_t"] < 0, str(e))
        check("但不换将", out["state"]["active_model"] == "champion")
    finally:
        _done(tmp, old, con)


def test_apply_and_record_original():
    print("\n[C5] 换将后名单按对手排序；存档里仍记系统自己的分")
    rows = [{"ticker": "A.AX", "short_score": 1.0, "ret_20d": 0.10, "short_chg_20d": -0.5},
            {"ticker": "B.AX", "short_score": -1.0, "ret_20d": -0.10, "short_chg_20d": 2.0}] + \
           [{"ticker": "X%d.AX" % i, "short_score": 0.0, "ret_20d": 0.0, "short_chg_20d": 0.0} for i in range(10)]
    sc = AR.challenger_scores(rows)
    out = CAL.apply_to_rows([dict(r) for r in rows], {"active_model": "short_build"}, sc)
    check("换将后 B 的看空分高于 A", out[1]["short_score"] > out[0]["short_score"])
    check("原分数保留在 short_score_original", out[0]["short_score_original"] == 1.0)
    tmp = tempfile.mkdtemp()
    old = AR._db_path
    AR._db_path = lambda: os.path.join(tmp, "a.db")
    try:
        AR.record_scores("2026-10-15", out)
        con = sqlite3.connect(os.path.join(tmp, "a.db"))
        v = con.execute("SELECT short_score FROM score_snapshots WHERE code='A'").fetchone()[0]
        con.close()
        check("存档记的是系统自己的分（才能继续给它对账）", v == 1.0)
    finally:
        AR._db_path = old
        shutil.rmtree(tmp, ignore_errors=True)
    same = CAL.apply_to_rows([dict(r) for r in rows], {"active_model": "champion"}, sc)
    check("没换将时名单原样不动", [r["short_score"] for r in same] == [r["short_score"] for r in rows])


def test_empty_archive():
    print("\n[C6] 全新克隆、存档为空：校准与前瞻成绩单都不报错，状态为默认")
    from asxquant import forward as FW
    px = _px(seed=6)
    tmp = tempfile.mkdtemp()
    old = AR._db_path
    AR._db_path = lambda: os.path.join(tmp, "a.db")
    try:
        out = CAL.run(px, {"mean_nonoverlap": -0.04, "se_nonoverlap": 0.02, "mean_ic": -0.03, "t_stat": -1.9})
        check("默认状态、无动作", out["state"] == {"active_model": "champion", "paused": False}
              and out["actions_today"] == [] and out["trust"]["prospective_segments"] == 0)
        check("把握 = 回测打五折的起点", abs(out["trust"]["ic"] - (-0.02)) < 1e-9, str(out["trust"]))
        f = FW.from_archive(px, list(px["close"].columns))
        check("前瞻成绩单为空也能出结构", f["days_recorded"] == 0 and f["short"]["20"]["verdict"] == "too_early")
    finally:
        AR._db_path = old
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("=" * 68)
    print("自动校准回归测试")
    print("=" * 68)
    for fn in (test_switch, test_pause, test_too_early_and_late, test_technical_v1_not_switchable,
               test_apply_and_record_original, test_empty_archive):
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
