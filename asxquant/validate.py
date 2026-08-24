# -*- coding: utf-8 -*-
"""Out-of-sample validation of the stock-level long / short scores.

A recommendation that has never been scored against history is an assertion, not a
signal. This module rebuilds the exact same score components as `picks.py`, but as
full time series, then runs the standard cross-sectional factor tests:

  * Information Coefficient -- daily rank correlation between score and the forward
    return. The forward return is measured over `horizon` days on EVERY trading day,
    so consecutive observations overlap and the naive t-statistic is 3-5x oversized.
    Every t reported here is Newey-West corrected (see `_overlap_stats`), with the
    naive value kept alongside as `t_naive` purely so the two can be compared.
  * Quantile spread -- forward returns of the top vs bottom score quintile.
  * A long/short portfolio -- top-N long, top-N short, rebalanced, net of costs.

Everything is lagged: the score uses data up to and including day t, and the return
measured starts at t+1. ASIC short data is already publication-lagged in `datafeed`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind
from .picks import LONG_WEIGHTS, SHORT_WEIGHTS


def _xz_frame(df, clip=3.0):
    """Cross-sectional z-score: each row standardised across tickers."""
    mu = df.mean(axis=1)
    sd = df.std(axis=1, ddof=0).replace(0, np.nan)
    return df.sub(mu, axis=0).div(sd, axis=0).clip(-clip, clip).fillna(0.0)


def _nw_variance(x, lag):
    """Newey-West long-run variance of a mean estimator (Bartlett kernel)."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)] 
    n = len(x)
    if n < 2:
        return np.nan, np.nan
    d = x - x.mean()
    g0 = float((d * d).sum() / n)
    if g0 <= 0:
        return np.nan, np.nan
    s = g0
    for k in range(1, min(lag, n - 1) + 1):
        s += 2.0 * (1.0 - k / (lag + 1.0)) * float((d[k:] * d[:-k]).sum() / n)
    s = max(s, g0 * 1e-6)          # a negative kernel sum is a small-sample artefact
    return s, float(np.sqrt(s / g0))


def _overlap_stats(series, horizon):
    """Honest inference for a daily statistic built on OVERLAPPING forward returns.

    This project measures a `horizon`-day forward return on EVERY trading day, so
    consecutive observations share (horizon-1)/horizon of their return window. The
    textbook t = mean / (sd / sqrt(n)) assumes independent draws and is therefore
    badly oversized here: measured lag-1 autocorrelation of the daily IC series runs
    around +0.9, and the naive standard error comes out 3-5x too small.

    Returns the naive t alongside a Newey-West t (Bartlett kernel, lag = horizon-1)
    and a second, assumption-free cross-check: split the series into `horizon`
    interleaved subsamples, each of which IS non-overlapping, and report the spread
    of their t-statistics. Read the Newey-West number, not the naive one.
    """
    s = pd.Series(series).dropna()
    n = len(s)
    if n < 60:
        return None
    m, sd = float(s.mean()), float(s.std(ddof=0))
    naive = m / (sd / np.sqrt(n)) if sd > 0 else np.nan
    lrv, infl = _nw_variance(s.values, max(1, horizon - 1))
    nw = m / np.sqrt(lrv / n) if np.isfinite(lrv) and lrv > 0 else np.nan

    sub = []
    for i in range(horizon):
        w = s.iloc[i::horizon]
        if len(w) > 30 and w.std(ddof=0) > 0:
            sub.append(float(w.mean() / (w.std(ddof=0) / np.sqrt(len(w)))))

    def _r(x, d=2):
        return round(float(x), d) if x is not None and np.isfinite(x) else None

    return {
        "mean": round(m, 5), "sd": round(sd, 5), "n_days": int(n),
        "t_naive": _r(naive), "t_stat": _r(nw),      # t_stat IS the Newey-West one
        "se_inflation": _r(infl, 1),
        "autocorr_1": _r(s.autocorr(1) if n > 2 else np.nan),
        "t_nonoverlap_median": _r(np.median(sub)) if sub else None,
        "t_nonoverlap_lo": _r(min(sub)) if sub else None,
        "t_nonoverlap_hi": _r(max(sub)) if sub else None,
        "verdict": _verdict(nw),
    }


def _verdict(t):
    """One label for a corrected t. Deliberately conservative at the boundary."""
    if t is None or not np.isfinite(t):
        return "insufficient"
    a = abs(t)
    if a >= 3.0:
        return "strong"
    if a >= 2.0:
        return "marginal"
    if a >= 1.6:
        return "weak"
    return "none"


VERDICT_CN = {
    "strong": "统计显著",
    "marginal": "勉强达标（边缘显著）",
    "weak": "微弱，达不到常规门槛",
    "none": "无统计显著性",
    "insufficient": "样本不足",
}


def build_factor_panel(px, short_pct, tickers, sector_map):
    """Time series of every score component, as date x ticker frames."""
    c = px["close"][tickers]
    h, l, v = px["high"][tickers], px["low"][tickers], px["volume"][tickers]
    a = px.get("adjclose", px["close"])[tickers]

    rng = (h - l).replace(0, np.nan)
    mfv = (((c - l) - (h - c)) / rng).fillna(0) * v
    cmf = mfv.rolling(20, min_periods=10).sum() / v.rolling(20, min_periods=10).sum().replace(0, np.nan)

    obv = (np.sign(a.diff().fillna(0)) * v).cumsum()
    obv_slope = (obv - obv.shift(20)) / v.rolling(60, min_periods=20).mean().replace(0, np.nan) / 20.0

    dv = c * v
    dv5 = dv.rolling(5, min_periods=2).mean()
    dv_z = (dv5 - dv5.rolling(120, min_periods=40).mean()) / \
        dv5.rolling(120, min_periods=40).std(ddof=0).replace(0, np.nan)

    mom20 = a / a.shift(20) - 1.0
    mom60 = a / a.shift(60) - 1.0
    ma50 = a / a.rolling(50, min_periods=25).mean() - 1.0
    ma200 = a / a.rolling(200, min_periods=100).mean() - 1.0

    d = a.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    dn = (-d).clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))

    hi = a.rolling(252, min_periods=100).max()
    lo = a.rolling(252, min_periods=100).min()
    pos52 = (a - lo) / (hi - lo).replace(0, np.nan)
    extension = ((pos52 - 0.5) * 2 + (ma200 / 0.20).clip(-2, 2) + (rsi - 50) / 20.0) / 3.0

    sp = short_pct.reindex(index=c.index, columns=tickers)
    sp_chg = sp - sp.shift(20)
    advol = v.rolling(20, min_periods=5).mean().replace(0, np.nan)

    # sector aggregates, broadcast back to each member column
    sect = pd.Series({t: sector_map.get(t, "na") for t in tickers})
    def _sector_mean(frame):
        out = pd.DataFrame(index=frame.index, columns=frame.columns, dtype=float)
        for s in sect.unique():
            cols = [t for t in tickers if sect[t] == s]
            if not cols:
                continue
            m = frame[cols].mean(axis=1)
            for t in cols:
                out[t] = m
        return out

    sector_flow = _xz_frame(_sector_mean(cmf) * 0.6 + _sector_mean(mom20) * 0.4)
    sector_short = _xz_frame(_sector_mean(sp_chg).fillna(0) * 0.6 - _sector_mean(cmf) * 0.4)

    return {
        "cmf": cmf, "obv": obv_slope, "turnover": dv_z, "mom20": mom20, "mom60": mom60,
        "above_ma50": ma50, "above_ma200": ma200, "rsi": rsi, "extension": extension,
        "short_pct": sp, "short_chg": sp_chg,
        "sector_flow": sector_flow, "sector_short": sector_short,
        "adv": advol * c, "adj": a,
    }


def composite_scores(F):
    """Rebuild long/short scores as time series using the live production weights."""
    z = {k: _xz_frame(F[k]) for k in
         ("cmf", "obv", "turnover", "mom20", "mom60", "above_ma50", "above_ma200",
          "short_pct", "short_chg", "extension")}
    overbought = (F["rsi"] - 72).clip(lower=0).fillna(0)

    lc = {
        "sector_flow": F["sector_flow"], "cmf": z["cmf"], "obv": z["obv"],
        "turnover": z["turnover"], "mom20": z["mom20"], "mom60": z["mom60"],
        "above_ma50": z["above_ma50"], "above_ma200": z["above_ma200"],
        "short_cover": -z["short_chg"], "short_low": -z["short_pct"],
        "not_extended": -_xz_frame(overbought),
    }
    sc = {
        "sector_short": F["sector_short"], "short_build": z["short_chg"],
        "short_level": z["short_pct"], "cmf_neg": -z["cmf"], "obv_neg": -z["obv"],
        "mom_neg": -z["mom20"], "below_ma": -z["above_ma50"],
        "extension": z["extension"],
        "squeeze_safe": pd.DataFrame(0.0, index=F["cmf"].index, columns=F["cmf"].columns),
    }
    long_s = sum(LONG_WEIGHTS[k] * lc[k] for k in LONG_WEIGHTS)
    short_s = sum(SHORT_WEIGHTS[k] * sc[k] for k in SHORT_WEIGHTS)
    return long_s, short_s


def _ic(score, fwd):
    """Daily cross-sectional Spearman IC between score and forward return."""
    s = score.rank(axis=1)
    f = fwd.rank(axis=1)
    valid = score.notna() & fwd.notna()
    s = s.where(valid)
    f = f.where(valid)
    n = valid.sum(axis=1)
    sm, fm = s.mean(axis=1), f.mean(axis=1)
    cov = ((s.sub(sm, axis=0)) * (f.sub(fm, axis=0))).sum(axis=1)
    den = np.sqrt((s.sub(sm, axis=0) ** 2).sum(axis=1) * (f.sub(fm, axis=0) ** 2).sum(axis=1))
    ic = (cov / den.replace(0, np.nan)).where(n >= 20)
    return ic.dropna()


def run_validation(px, short_pct, tickers, sector_map, horizon=20,
                   n_side=3, cost_bps=15.0, min_adv=3e6):
    """Full cross-sectional scorecard for the long and short scores."""
    F = build_factor_panel(px, short_pct, tickers, sector_map)
    long_s, short_s = composite_scores(F)

    a = F["adj"]
    fwd = a.shift(-horizon) / a - 1.0
    r1 = a.pct_change()

    liquid = F["adv"] > min_adv
    # short interest only exists from 2022, so restrict to where the panel is real
    have = F["short_pct"].notna()
    mask = liquid & have & a.notna()
    start = mask.sum(axis=1).ge(30).idxmax()

    L = long_s.where(mask).loc[start:]
    S = short_s.where(mask).loc[start:]
    FW = fwd.loc[start:]

    ic_l, ic_s = _ic(L, FW), _ic(S, FW)

    def _ic_stats(ic):
        st = _overlap_stats(ic, horizon)
        if st is None:
            return None
        st["mean_ic"] = st.pop("mean")
        st["ic_std"] = st.pop("sd")
        st["hit_rate"] = round(float((ic > 0).mean()), 3)
        st["verdict_cn"] = VERDICT_CN.get(st["verdict"], st["verdict"])
        return st

    # quintile spread on the long score
    def _quintile(score):
        q = score.rank(axis=1, pct=True)
        top = FW.where(q >= 0.8).mean(axis=1)
        bot = FW.where(q <= 0.2).mean(axis=1)
        d = pd.concat([top.rename("top"), bot.rename("bot")], axis=1).dropna()
        if len(d) < 60:
            return None
        # the daily spread series overlaps exactly like the IC series does
        st = _overlap_stats(d["top"] - d["bot"], horizon) or {}
        return {"top": round(float(d["top"].mean()), 5),
                "bottom": round(float(d["bot"].mean()), 5),
                "spread": round(float((d["top"] - d["bot"]).mean()), 5),
                "t_stat": st.get("t_stat"), "t_naive": st.get("t_naive"),
                "verdict": st.get("verdict"),
                "verdict_cn": VERDICT_CN.get(st.get("verdict"), None),
                "n": int(len(d))}

    # ---- portfolios, rebalanced every `horizon` days -------------------
    # Weights are written onto the days AFTER the score date, so they are already
    # lagged correctly. Do not shift them again -- doing so double-lags the book.
    dates = L.index
    posL = pd.DataFrame(0.0, index=dates, columns=L.columns)
    posS = posL.copy()
    for i in range(0, len(dates), horizon):
        d0 = dates[i]
        d1 = dates[min(i + horizon, len(dates) - 1)]
        lrow, srow = L.loc[d0].dropna(), S.loc[d0].dropna()
        if len(lrow) < 20 or len(srow) < 20:
            continue
        lw = list(lrow.nlargest(n_side).index)
        sw = [t for t in srow.nlargest(n_side).index if t not in lw]
        if not lw or not sw:
            continue
        seg = (dates > d0) & (dates <= d1)
        for t in lw:
            posL.loc[seg, t] = 1.0 / len(lw)
        for t in sw:
            posS.loc[seg, t] = 1.0 / len(sw)

    rr = r1.reindex(index=dates, columns=L.columns).fillna(0.0)
    held = posL.abs().add(posS.abs()) > 0
    mkt = rr.where(mask.loc[start:]).mean(axis=1).fillna(0.0)

    gL = (posL * rr).sum(axis=1)
    gS = (posS * rr).sum(axis=1)
    turn = (posL.diff().abs().sum(axis=1) + posS.diff().abs().sum(axis=1)).fillna(0.0)
    ls_gross = 0.5 * gL - 0.5 * gS
    ls_net = ls_gross - 0.5 * turn * (cost_bps / 1e4)

    def _stats(r, label):
        eq = (1 + r).cumprod()
        yrs = max(len(r) / 252.0, 1e-9)
        vol = float(r.std(ddof=0) * np.sqrt(252))
        cagr = float(eq.iloc[-1] ** (1 / yrs) - 1) if eq.iloc[-1] > 0 else -1.0
        return {"label": label, "cagr": round(cagr, 4), "vol": round(vol, 4),
                "sharpe": round(cagr / vol, 2) if vol > 1e-9 else None,
                "max_dd": round(float((eq / eq.cummax() - 1).min()), 4),
                "final_equity": round(float(eq.iloc[-1]), 3)}

    eq_ls = (1 + ls_net).cumprod()
    eq_mkt = (1 + mkt).cumprod()
    yrs = max(len(dates) / 252.0, 1e-9)

    return {
        "horizon": horizon, "n_side": n_side, "cost_bps": cost_bps,
        "start": str(dates[0].date()), "end": str(dates[-1].date()),
        "years": round(yrs, 1),
        "universe_median": int(mask.loc[start:].sum(axis=1).median()),
        "ic_long": _ic_stats(ic_l),
        "ic_short": _ic_stats(ic_s),
        "quintile_long": _quintile(L),
        "quintile_short": _quintile(S),
        "legs": {
            "long": _stats(gL, "做多篮子"),
            "short_basket": _stats(gS, "做空篮子(其自身涨跌)"),
            "market": _stats(mkt, "等权市场"),
            "long_short_net": _stats(ls_net, "市场中性多空(扣成本)"),
            "long_short_gross": _stats(ls_gross, "市场中性多空(毛)"),
        },
        "turnover_pa": round(float(turn.mean() * 252), 1),
        "curve": {
            "dates": [str(d.date()) for d in dates[::5]],
            "ls": [round(float(x), 4) for x in eq_ls.values[::5]],
            "ew": [round(float(x), 4) for x in eq_mkt.values[::5]],
            "lng": [round(float(x), 4) for x in (1 + gL).cumprod().values[::5]],
            "sht": [round(float(x), 4) for x in (1 + gS).cumprod().values[::5]],
        },
    }
