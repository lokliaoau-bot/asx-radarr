# -*- coding: utf-8 -*-
"""Where the short sellers got in, and whether they are currently winning.

WHAT AUSTRALIA DOES NOT PUBLISH
-------------------------------
There is no way to name a short seller on the ASX. ASIC's daily file is
AGGREGATE ONLY -- `date, code, short_shares, short_pct` and nothing else. Unlike
the EU/UK (ESMA and FCA publish individual holders above 0.5% by name) or Japan,
Australia has no public register of who is short, and no disclosure of the price
anyone shorted at. Substantial-holder notices (603/604/605) name LONG holders of
5%+ and say nothing about short positions; where a hedge fund does appear in one
it is normally the securities-lending leg, not a disclosed short.

So "which fund is short this stock, and at what price" cannot be answered from
any free Australian source, and no amount of parsing fixes that.

WHAT CAN BE RECONSTRUCTED
-------------------------
The aggregate position is disclosed DAILY, so its day-to-day changes are visible.
Treating the market's total short position as one FIFO inventory -- each day's
increase opens a lot at that day's typical price, each day's decrease closes the
oldest lots first -- yields the volume-weighted average entry price of the short
interest that is STILL OPEN, and therefore whether shorts are collectively sitting
on a gain or a loss. Shorts under water are the precondition for a squeeze, which
is the part of this the user actually cares about.

Measured on this universe, FIFO prices a median 98% of the currently open position;
the remainder is legacy stock already outstanding when ASIC's file begins in 2022
and can never be costed. `coverage` reports that fraction per stock -- read the
cost estimate as unreliable when it is low (FLT today: 62%).

CAVEATS THAT MUST STAY VISIBLE
------------------------------
* Aggregate, not per-holder. This is the average of everyone short, not any one
  desk's book. Nobody actually holds "the" position at this price.
* Each day's additions are priced at that day's typical price (H+L+C)/3. Real
  fills are spread through the session; intraday timing is unknowable here.
* ASIC publishes T+4, so the newest point is four business days old. That is a
  property of the disclosure regime, not of this code.
* Computed in dividend-adjusted space (this project's trap #1) and converted back
  to today's quoted price for display. A short seller pays the dividend, so the
  adjusted-space comparison is the economically correct one.
* This is a MEASUREMENT. It is not scored and feeds no ranking.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MIN_DAYS = 60
MAX_LOTS = 6000


def _fifo(changes, prices, max_lots=MAX_LOTS):
    """Run the aggregate position as one FIFO inventory. Returns the open lots."""
    lots = []                                   # each: [shares, price, date]
    for dt_, chg in changes.items():
        if not np.isfinite(chg) or chg == 0:
            continue
        p = prices.get(dt_, np.nan)
        if chg > 0:
            if not np.isfinite(p):
                continue
            lots.append([float(chg), float(p), dt_])
            if len(lots) > max_lots:            # merge the oldest tail, keep the cost exact
                head = lots[:len(lots) - max_lots + 1]
                q = sum(x[0] for x in head)
                if q > 0:
                    lots = [[q, sum(x[0] * x[1] for x in head) / q, head[0][2]]] + lots[len(head):]
        else:
            need = -float(chg)
            while need > 1e-9 and lots:         # covering closes the oldest lots first
                take = min(need, lots[0][0])
                lots[0][0] -= take
                need -= take
                if lots[0][0] <= 1e-9:
                    lots.pop(0)
    return lots


def short_cost(code, shorts, px, lookback_adds=120, n_adds=3, group=None):
    """Estimated entry price of the short interest still open in `code`.

    `group` lets the caller pass an already-sliced frame for this code. Filtering
    1.8M rows once per name is what made the first version take 9s for 110 stocks.
    """
    tk = code + ".AX"
    close = px["close"]
    if tk not in close.columns:
        return None
    s = group if group is not None else shorts[shorts["code"] == code]
    s = s.sort_values("date")
    if len(s) < MIN_DAYS:
        return None
    qty_s = s.set_index("date")["short_shares"].dropna()
    if len(qty_s) < MIN_DAYS:
        return None

    adj = px.get("adjclose", close)[tk]
    f = (adj / close[tk].replace(0, np.nan)).ffill()
    typical = ((px["high"][tk] + px["low"][tk] + close[tk]) / 3.0 * f).reindex(qty_s.index).ffill()

    lots = _fifo(qty_s.diff(), typical)
    priced = sum(x[0] for x in lots)
    actual = float(qty_s.iloc[-1])
    if priced <= 0 or actual <= 0:
        return None

    live = adj.notna() & close[tk].notna() & (close[tk] != 0)
    if not bool(live.any()):
        return None
    at = live[live].index[-1]
    px_now, f_now = float(adj.loc[at]), float(adj.loc[at] / close[tk].loc[at])
    if not (np.isfinite(f_now) and f_now > 0 and np.isfinite(px_now) and px_now > 0):
        return None

    cost_adj = sum(x[0] * x[1] for x in lots) / priced
    if not (np.isfinite(cost_adj) and cost_adj > 0):
        return None

    # A short seller gains when price falls below entry.
    pnl = float(cost_adj / px_now - 1.0)

    d = qty_s.diff().tail(lookback_adds)
    ups = d[d > 0].sort_values(ascending=False).head(n_adds)
    adds = [{"date": str(pd.Timestamp(i).date()),
             "shares": int(v),
             "pct_of_pos": round(float(v / actual), 4),
             "px": round(float(typical.get(i, np.nan)) / f_now, 4)}
            for i, v in ups.items() if np.isfinite(typical.get(i, np.nan))]

    return {
        "code": code,
        "cost": float(cost_adj / f_now),
        "px": float(px_now / f_now),
        "pnl": round(pnl, 5),                    # >0 空头浮盈, <0 空头浮亏(轧空压力)
        "coverage": round(float(priced / actual), 3),
        "shares": int(actual),
        "short_pct": float(s["short_pct"].iloc[-1]),
        "since": str(pd.Timestamp(lots[0][2]).date()) if lots else None,
        "as_of": str(pd.Timestamp(qty_s.index[-1]).date()),
        "adds": adds,
    }


def build(shorts, px, codes, min_short_pct=1.0):
    """Cost basis for every name carrying a short position worth talking about."""
    if shorts is None or not len(shorts):
        return {}
    last = shorts[shorts["date"] == shorts["date"].max()]
    keep = set(last[last["short_pct"] >= min_short_pct]["code"]) & set(codes)
    if not keep:
        return {}
    sub = shorts[shorts["code"].isin(keep)]
    out = {}
    for code, g in sub.groupby("code", sort=False):
        try:
            r = short_cost(code, shorts, px, group=g)
        except Exception:
            r = None
        if r:
            out[code] = r
    return out


def note(r):
    """One plain-language line. Describes the disclosed position, predicts nothing."""
    if not r:
        return None
    who = "赌它跌的机构"
    if r["pnl"] < -0.05:
        tail = "现在**亏着** %.0f%%——他们越亏，越可能被迫买回股票止损（轧空）" % abs(r["pnl"] * 100)
    elif r["pnl"] > 0.05:
        tail = "现在赚着 %.0f%%，还没有平仓压力" % (r["pnl"] * 100)
    else:
        tail = "目前基本打平"
    base = "%s平均在 A$%.2f 附近建的仓（占股本 %.2f%%），%s" % (
        who, r["cost"], r["short_pct"], tail)
    if r["coverage"] < 0.8:
        base += "（注：只有 %.0f%% 的仓位能追溯到建仓价，其余是2022年前的老仓）" % (r["coverage"] * 100)
    return base
