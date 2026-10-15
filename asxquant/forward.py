# -*- coding: utf-8 -*-
"""前瞻成绩单：系统每天**当时**记下的名单，之后真实表现如何。

回测再严谨，也是拿今天的代码、今天的数据去「重演」过去。唯一不能被事后修饰的
证据，是系统在信号日**当天、下一个交易日开盘之前**就写进存档的名单——存档提交在
仓库里，记录时刻早于结果，事后改不了。这里只做一件事：给这些记录打分。

口径（与 validate.run_validation 对齐，不另起炉灶）：
  · 进场 = 信号日之后第 1 个交易日收盘（复权价），出场 = 再往后第 h 个交易日收盘。
  · 对照 = 同一进出场口径下，股票池里**信号日 20 日均成交额 > A$300 万**、且进场日有成交的
    股票的等权平均——与 validate 的「等权市场」用同一个流动性门槛（validate 另外要求有 ASIC
    空头数据；名单本身就来自有空头数据的股票，所以这里不再重复要求）。
  · 超额 = 名单平均 − 对照。看空名单超额 < 0 才算「对」；看多名单 > 0 才算「对」。
  · 进场日没有成交（停牌）的名字不计入，单独计数——不当作 0 收益。
  · 记录时刻晚于下一个交易日开盘（悉尼 10:00）的，标「迟到」，**不计分**：那时价格
    已经开始动了，不是前瞻。recorded_at 没有时区，按 UTC 解读（云端运行就是 UTC；
    若是悉尼本地时间，按 UTC 读只会把它算得更晚——偏保守，不会把迟到的放进来）。

检验：每天的名单大多重复、持有期互相重叠，不能把每天当一个独立样本。只取**持有期
互不重叠**的那些日子（从最早一天起贪心选取）做 t 检验；不重叠样本少于 10 段时
一律判「还太早」，不给结论。

多重检验（预先登记）：每边 5/10/20 日三个持有期一起看，p 值乘 3（Bonferroni）后才和 0.05 比。
主判据是**看空名单 20 日**——系统本来就按 20 日调仓；其余格子只作参考。
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

SYDNEY = ZoneInfo("Australia/Sydney")
HORIZONS = (5, 10, 20)
MIN_NONOVERLAP = 10
MIN_ADV_AUD = 3e6          # 与 validate.run_validation 的 min_adv 相同
PRIMARY = ("short", 20)


def _next_open_utc(day):
    """下一个交易日开盘（悉尼 10:00）的 UTC 时刻；day 是 Timestamp（无时区）。"""
    local = dt.datetime.combine(day.date(), dt.time(10, 0), SYDNEY)
    return local.astimezone(dt.timezone.utc)


def _parse_recorded(s):
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(str(s))
    except ValueError:
        return None
    return t.replace(tzinfo=dt.timezone.utc) if t.tzinfo is None else t.astimezone(dt.timezone.utc)


def score_records(px, records, universe, h, side):
    """records: [{'asof': 'YYYY-MM-DD', 'picks': [ticker...], 'recorded_at': str}]

    返回逐条结果（含 pending / late / no_price_day / unresolved 状态），不丢任何一条。
    """
    A = px.get("adjclose", px["close"])
    V = px.get("volume")
    idx = A.index
    adv = None
    if V is not None:
        adv = (px["close"] * V).reindex(columns=universe).rolling(20, min_periods=5).mean()
    pos = {d: i for i, d in enumerate(idx)}
    uni = [t for t in universe if t in A.columns]
    out = []
    for rec in sorted(records, key=lambda r: r["asof"]):
        row = {"asof": rec["asof"], "picks": [p.replace(".AX", "") for p in rec["picks"]],
               "status": None, "excess": None, "basket": None, "pool": None,
               "picks_used": 0, "picks_untradable": 0}
        d = pd.Timestamp(rec["asof"])
        i = pos.get(d)
        if i is None:
            row["status"] = "no_price_day"
            out.append(row)
            continue
        if i + 1 >= len(idx):
            row["status"] = "pending"
            out.append(row)
            continue
        rt = _parse_recorded(rec.get("recorded_at"))
        if rt is None or rt >= _next_open_utc(idx[i + 1]):
            row["status"] = "late"
            out.append(row)
            continue
        e, x = i + 1, i + 1 + h
        if x >= len(idx):
            row["status"] = "pending"
            row["exit_due"] = None
            out.append(row)
            continue
        entry, exit_ = A[uni].iloc[e], A[uni].iloc[x]
        traded = entry.notna() & (entry > 0)
        if adv is not None:
            liquid = adv.reindex(columns=uni).iloc[i] > MIN_ADV_AUD
        else:
            liquid = traded
        picks_set = {p if p.endswith(".AX") else p + ".AX" for p in rec["picks"]}
        if V is not None:
            vv = V.reindex(columns=uni).iloc[e]
            traded &= ~(vv == 0)
        r = (exit_ / entry - 1.0).where(traded)
        r = r[np.isfinite(r)]
        picks = sorted(picks_set)
        got = [r[p] for p in picks if p in r.index]
        row["picks_untradable"] = len(picks) - len(got)
        pool_r = r[[t for t in r.index if bool(liquid.get(t, False))]]
        if not got or len(pool_r) < 20:
            row["status"] = "unresolved"
            out.append(row)
            continue
        basket, pool = float(np.mean(got)), float(pool_r.mean())
        row.update(status="matured", basket=round(basket, 5), pool=round(pool, 5),
                   excess=round(basket - pool, 5), picks_used=len(got),
                   entry=str(idx[e].date()), exit=str(idx[x].date()),
                   _e=e, _x=x)
        out.append(row)
    return out


def summarize(rows, h, side):
    from scipy import stats as st
    m = [r for r in rows if r["status"] == "matured"]
    good = (lambda v: v < 0) if side == "short" else (lambda v: v > 0)
    res = {"horizon": h, "side": side,
           "recorded": len(rows),
           "matured": len(m),
           "pending": sum(r["status"] == "pending" for r in rows),
           "late": sum(r["status"] == "late" for r in rows),
           "unresolved": sum(r["status"] in ("unresolved", "no_price_day") for r in rows),
           "mean_excess": None, "hit_rate": None, "nonoverlap_n": 0,
           "t": None, "p": None, "p_adj": None,
           "primary": (side, h) == PRIMARY, "verdict": "too_early"}
    if m:
        ex = np.array([r["excess"] for r in m])
        res["mean_excess"] = round(float(ex.mean()), 5)
        res["hit_rate"] = round(float(np.mean([good(v) for v in ex])), 3)
    # 持有期互不重叠的子样本：下一段的进场日必须晚于上一段的出场日
    sel, last_exit = [], -1
    for r in m:
        if r["_e"] > last_exit:
            sel.append(r["excess"])
            last_exit = r["_x"]
    res["nonoverlap_n"] = len(sel)
    if len(sel) >= 3 and np.std(sel, ddof=1) > 0:
        t = float(np.mean(sel) / (np.std(sel, ddof=1) / np.sqrt(len(sel))))
        res["t"] = round(t, 2)
        p = float(2 * st.t.sf(abs(t), len(sel) - 1))
        res["p"] = round(p, 3)
        res["p_adj"] = round(min(1.0, p * len(HORIZONS)), 3)
    if len(sel) >= MIN_NONOVERLAP and res["p_adj"] is not None:
        right = good(np.mean(sel))
        res["verdict"] = ("confirmed" if right and res["p_adj"] < 0.05 else
                          "consistent" if right else "wrong_way")
    return res


def track(px, records, universe, side, horizons=HORIZONS, keep=40):
    """一个名单（看空或看多）在 5/10/20 日上的前瞻成绩。"""
    out = {}
    for h in horizons:
        rows = score_records(px, records, universe, h, side)
        s = summarize(rows, h, side)
        s["rows"] = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows[-keep:]]
        out[str(h)] = s
    return out


def from_shortradar_archive(px, universe, con=None):
    """看空雷达（technical-v1，10 日口径）每天排名前五分之一，给它打前瞻分。

    只评价澳股雷达股票池里的名字（池外的自选股没有复权价，混进来口径不一）。
    """
    from . import archive as AR
    own = con is None
    con = con or AR._connect()
    try:
        rows = con.execute("SELECT asof, code, rank, eligible_n, recorded_at "
                           "FROM shortradar_snapshots ORDER BY asof, rank").fetchall()
    finally:
        if own:
            con.close()
    uni = set(universe)
    by = {}
    for asof, code, rank, n, rec in rows:
        top = max(1, int(np.ceil(n / 5.0)))
        d = by.setdefault(asof, {"asof": asof, "picks": [], "recorded_at": rec})
        if rank <= top and code + ".AX" in uni:
            d["picks"].append(code + ".AX")
    recs = [r for r in by.values() if r["picks"]]
    return {"since": min(by) if by else None, "days_recorded": len(by),
            "picks_rule": "每天 10 日口径排名的前五分之一（只算澳股雷达股票池里的）",
            "short": track(px, recs, list(universe), "short")}


def from_archive(px, universe, con=None):
    """读 archive.market_snapshots 的每日三只名单，给看空/看多两边打分。"""
    from . import archive as AR
    own = con is None
    con = con or AR._connect()
    try:
        rows = con.execute("SELECT asof, short_picks, long_picks, recorded_at "
                           "FROM market_snapshots ORDER BY asof").fetchall()
    finally:
        if own:
            con.close()
    def recs(col):
        return [{"asof": a, "picks": [c + ".AX" for c in (p or "").split(",") if c],
                 "recorded_at": r} for a, s_, l_, r in rows
                for p in [(s_ if col == "short" else l_)] if p]
    return {"since": rows[0][0] if rows else None, "days_recorded": len(rows),
            "method": ("进场=信号日后第1个交易日收盘，出场=再往后第h个交易日收盘（复权）；"
                       "对照=股票池当天有成交的股票等权；只用持有期互不重叠的日子做检验，"
                       "少于%d段判「还太早」" % MIN_NONOVERLAP),
            "short": track(px, recs("short"), universe, "short"),
            "long": track(px, recs("long"), universe, "long")}
