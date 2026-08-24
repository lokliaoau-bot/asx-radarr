# -*- coding: utf-8 -*-
"""Smart-money layer: the accumulation signature (S4) and the money-in / money-out panel.

Built to the two specs in D:\\Download, with their own judgment gates actually run
rather than assumed. What survived those gates, measured on this data set:

  S4 accumulation signature   RETESTED 2026-08-24 and DEMOTED. The original verdict
                              (IC +0.0138, t=6.93) used a naive t-statistic on daily
                              observations of an OVERLAPPING 60-day forward return.
                              Newey-West corrected, the same test gives t = +1.07 for
                              raw S4 and t = +0.45 for the rolling-z form this module
                              actually uses -- and the 2022+ subsample is negative.
                              It therefore FAILS the spec's "no positive expectancy
                              => no weight" rule and now carries NO weight in the
                              panel ranking. It is still computed and still shown on
                              the card, as a description of the price-volume shape.

  F2 sector short-interest    Lead-lag peak lands at lag +4 weeks, not a negative
      as a sector timer       lag. Short changes FOLLOW sector price rather than
                              lead it, so sector flow is presented as a measurement
                              of the present, never as a sector forecast.

  Stock-level short score     Cross-sectional IC t = -7.3 (validated in validate.py).
                              This is why the stock lists inside each sector are
                              trustworthy even though the sector ranking is not a
                              forecast.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind

CLIP = 1.5
WIN = 25
BASE = 250


def _clip_log(x, lo=-CLIP, hi=CLIP):
    return np.log(x.replace(0, np.nan)).clip(lo, hi)


def accumulation_signature(close, high, low, volume, adj, win=WIN, base=BASE):
    """S4 — institutions absorbing supply without moving price.

    Rising volume, narrow price range, falling volatility, improving Amihud depth.
    The spec is explicit that the four terms must be logged and clipped INDIVIDUALLY
    rather than multiplied: a four-way product of unbounded ratios has exploding
    variance and diverges as Amihud approaches zero.
    """
    ret = adj.pct_change()
    rel_volume = (volume.rolling(win, min_periods=win // 2).mean() /
                  volume.rolling(base, min_periods=base // 2).mean())
    rng = ((adj.rolling(win, min_periods=win // 2).max() -
            adj.rolling(win, min_periods=win // 2).min()) /
           adj.rolling(win, min_periods=win // 2).mean())
    rv_s = ret.rolling(win, min_periods=win // 2).std()
    rv_l = ret.rolling(base, min_periods=base // 2).std()
    vol_ratio = rv_s / rv_l.replace(0, np.nan)

    dv = (close * volume).replace(0, np.nan)
    ami = ret.abs() / dv
    ami_now = ami.rolling(win, min_periods=win // 2).mean()
    ami_base = ami.rolling(base, min_periods=base // 2).mean()

    terms = [
        _clip_log(rel_volume),
        (-np.log(rng.clip(lower=0.02))).clip(-CLIP, CLIP),
        (-np.log(vol_ratio.clip(lower=0.3))).clip(-CLIP, CLIP),
        _clip_log(ami_base / ami_now.clip(lower=1e-12)),
    ]
    return sum(terms) / len(terms)


def _xz(vals, clip=3.0):
    v = np.array([np.nan if x is None else float(x) for x in vals], dtype=float)
    ok = np.isfinite(v)
    if ok.sum() < 4:
        return np.zeros_like(v)
    mu, sd = np.nanmean(v[ok]), np.nanstd(v[ok])
    if not np.isfinite(sd) or sd == 0:
        return np.zeros_like(v)
    z = (v - mu) / sd
    z[~ok] = 0.0
    return np.clip(z, -clip, clip)


# ----------------------------------------------------------------------
def build_profiles(px, tickers, win=60):
    """Volume-by-price for every name, keyed by ticker. Cheap: ~40 bins x 60 days each.

    Kept out of every score on purpose. The cross-sectional test on overhead supply
    first looked strong (60-day IC t = +7.7), but that was the naive t-statistic on
    daily observations of an overlapping 60-day forward return. Newey-West corrected
    it is t = +1.6, and the non-overlapping cross-check gives +1.2 -- no demonstrated
    expectancy at all. Under this project's own rule that earns a place on the card
    as a measurement of where the crowd's cost sits, and no place in any score.
    """
    closes, highs, lows, vols = px["close"], px["high"], px["low"], px["volume"]
    adj = px.get("adjclose", closes)
    out = {}
    for t in tickers:
        if t not in closes.columns or t not in adj.columns:
            continue
        try:
            r = ind.volume_profile(highs[t], lows[t], vols[t], closes[t], adj[t], win=win)
        except Exception:
            r = None
        if r:
            out[t] = r
    return out


def _px(v):
    """Penny stocks need three decimals; everything else reads better with two."""
    return ("A$%.3f" if abs(v) < 1 else "A$%.2f") % v


def profile_note(p):
    """One plain-language clause about the crowd's cost. Describes, never predicts."""
    if not p:
        return None
    vs = p.get("vs_cost")
    if vs is None:
        return None
    # Only the cost comparison belongs here. The overhead percentage is already on
    # the cost map's own legend directly below this line -- saying it twice just
    # makes the card longer on a phone.
    return "现价比过去3个月大家的平均成本%s %.1f%%（那个价位约 %s）" % (
        "低" if vs < 0 else "高", abs(vs * 100), _px(p["cost"]))


def _stock_flow_rows(panel, px, s4_last, s4_z_last):
    """Flatten every constituent with the numbers the panel needs."""
    rows = []
    for key, p in panel.items():
        for s in p["stocks"]:
            t = s["ticker"]
            r = dict(s)
            r["sector"] = key
            r["sector_name"] = p["name"]
            r["sector_flow"] = p["flow"]["score"]
            r["s4"] = float(s4_last.get(t, np.nan))
            r["s4_z"] = float(s4_z_last.get(t, np.nan))
            rows.append(r)
    return rows


def _net_dollar_flow(close, high, low, volume, days=20):
    """Signed dollar money flow over `days`, in AUD.

    Close-location value x turnover: the standard daily-bar stand-in for Lee-Ready
    trade signing. This is an estimate of buying vs selling pressure, not settled
    money -- Australia has no equivalent of the HK Stock Connect holdings file.
    """
    rng = (high - low).replace(0, np.nan)
    clv = ((close - low) - (high - close)) / rng
    return (clv.fillna(0) * volume * close).tail(days).sum()


def build_money_flow_panel(panel, px, top_sectors=3, top_stocks=4, profiles=None,
                           halted=None, short_cost=None):
    """The headline panel: where money is arriving, where it is leaving, by name.

    Deliberately framed as MEASUREMENT. The sector lead-lag test failed, so this
    answers "where is money right now", not "which sector will go up next".
    """
    closes, highs, lows, vols = px["close"], px["high"], px["low"], px["volume"]
    adj = px.get("adjclose", closes)
    tk = [t for t in closes.columns if t in adj.columns]

    s4 = accumulation_signature(closes[tk], highs[tk], lows[tk], vols[tk], adj[tk])
    s4_last = s4.iloc[-1] if len(s4) else pd.Series(dtype=float)
    s4_mu = s4.rolling(BASE, min_periods=80).mean().iloc[-1] if len(s4) > 80 else s4_last * 0
    s4_sd = s4.rolling(BASE, min_periods=80).std().iloc[-1] if len(s4) > 80 else s4_last * 0 + 1
    s4_z_last = ((s4_last - s4_mu) / s4_sd.replace(0, np.nan)).clip(-3, 3)

    rows = _stock_flow_rows(panel, px, s4_last, s4_z_last)
    if not rows:
        return None

    # per-stock 20d net dollar flow (AUD) and volume-by-price
    for r in rows:
        t = r["ticker"]
        try:
            r["net_flow_20d"] = float(_net_dollar_flow(
                closes[t], highs[t], lows[t], vols[t], 20))
        except Exception:
            r["net_flow_20d"] = np.nan
        r["profile"] = (profiles or {}).get(t)
        r["short_cost"] = (short_cost or {}).get(r["code"])

    g = lambda f: [r.get(f) for r in rows]                      # noqa: E731
    caps = np.array([(r.get("adv_aud") or 0) for r in rows], dtype=float)
    flow_scaled = np.array([r["net_flow_20d"] for r in rows], dtype=float) / np.where(caps > 0, caps, np.nan)

    # S4 was dropped from both rankings on 2026-08-24 -- see the module docstring.
    # The surviving terms are all DIRECT measurements (signed dollar flow, Chaikin
    # money flow, turnover, disclosed short changes, realised return); their original
    # weights are simply renormalised to sum to 1, so no new judgment is introduced.
    z_in = (0.343 * _xz(flow_scaled) + 0.257 * _xz(g("cmf20")) +
            0.200 * _xz(g("dollar_vol_z")) - 0.200 * _xz(g("short_chg_20d")))
    z_out = (-0.310 * _xz(g("cmf20")) + 0.286 * _xz(g("short_chg_20d")) -
             0.238 * _xz(flow_scaled) - 0.167 * _xz(g("ret_20d")))
    sd_in, sd_out = (np.std(z_in) or 1.0), (np.std(z_out) or 1.0)

    for i, r in enumerate(rows):
        r["in_score"] = round(float(z_in[i] / sd_in), 3)
        r["out_score"] = round(float(z_out[i] / sd_out), 3)

    # A suspended name cannot be receiving or losing money today, whatever its
    # frozen indicators say, so it never appears on this panel.
    _halted = halted or set()
    liq = [r for r in rows if (r.get("adv_aud") or 0) >= 1_000_000
           and r["ticker"] not in _halted]
    by_sector = {}
    for r in liq:
        by_sector.setdefault(r["sector"], []).append(r)

    def _sector_block(keys, side):
        out = []
        for k in keys:
            p = panel[k]
            mem = by_sector.get(k, [])
            if not mem:
                continue
            fld = "in_score" if side == "in" else "out_score"
            picks = sorted(mem, key=lambda r: -r[fld])[:top_stocks]
            net = float(np.nansum([r["net_flow_20d"] for r in mem]))
            out.append({
                "sector": k, "name": p["name"],
                "flow_score": p["flow"]["score"],
                "heat_score": p["heat"]["score"],
                "stage": p["stage"]["label"],
                "ret_20d": p["perf"]["ret_20d"],
                "short_pct": p["raw"]["short_pct"],
                "short_chg_20d": p["raw"]["short_chg_20d"],
                "net_flow_20d_m": net / 1e6,
                "stocks": [_pack_stock(r, side) for r in picks],
            })
        return out

    order_in = sorted(panel, key=lambda k: -panel[k]["flow"]["score"])[:top_sectors]
    order_out = sorted(panel, key=lambda k: panel[k]["flow"]["score"])[:top_sectors]

    return {
        "as_of": None,
        "inflow": _sector_block(order_in, "in"),
        "outflow": _sector_block(order_out, "out"),
        "note_in": "钱正在进这些地方",
        "note_out": "钱正在从这些地方撤出",
    }


def _pack_stock(r, side):
    return {
        "code": r["code"], "ticker": r["ticker"], "px": r["px"],
        "score": r["in_score"] if side == "in" else r["out_score"],
        "net_flow_20d_m": (r["net_flow_20d"] / 1e6) if np.isfinite(r.get("net_flow_20d", np.nan)) else None,
        "ret_5d": r["ret_5d"], "ret_20d": r["ret_20d"], "rsi14": r["rsi14"],
        "cmf20": r["cmf20"], "dollar_vol_z": r["dollar_vol_z"],
        "short_pct": r["short_pct"], "short_chg_20d": r["short_chg_20d"],
        "days_to_cover": r["days_to_cover"], "adv_aud": r["adv_aud"],
        "s4": round(float(r["s4"]), 3) if np.isfinite(r.get("s4", np.nan)) else None,
        "s4_z": round(float(r["s4_z"]), 2) if np.isfinite(r.get("s4_z", np.nan)) else None,
        "profile": r.get("profile"),
        "short_cost": r.get("short_cost"),
        "why": _why(r, side),
    }


def _aud(v):
    """Australian convention: millions, not the Chinese 万."""
    a = abs(v)
    if a >= 1e9:
        return "A$%.2f 十亿" % (v / 1e9)
    if a >= 1e6:
        return "A$%.0f 百万" % (v / 1e6)
    return "A$%.0f 千" % (v / 1e3)


def _why(r, side):
    """One short plain-language clause naming the strongest driver."""
    out = []
    sc, dv, s4z = r.get("cmf20"), r.get("dollar_vol_z"), r.get("s4_z")
    chg, sp = r.get("short_chg_20d"), r.get("short_pct")
    f = r.get("net_flow_20d")

    if side == "in":
        if np.isfinite(s4z or np.nan) and s4z > 0.8:
            out.append("成交在放大、价格却没被拉高——像有人在悄悄收货")
        if np.isfinite(f or np.nan) and f > 0:
            out.append("近20天买盘净流入约 %s" % _aud(f))
        if np.isfinite(sc or np.nan) and sc > 0.05:
            out.append("买盘明显强于卖盘")
        if np.isfinite(chg or np.nan) and chg < -0.15:
            out.append("赌它跌的机构在撤退（20天减 %.2f 个百分点）" % abs(chg))
        if np.isfinite(dv or np.nan) and dv > 1.0:
            out.append("成交额比平常明显放大")
    else:
        if np.isfinite(chg or np.nan) and chg > 0.15:
            out.append("机构在加码赌它跌（20天增 %.2f 个百分点，现 %.2f%%）" % (chg, sp or 0))
        if np.isfinite(f or np.nan) and f < 0:
            out.append("近20天卖盘净流出约 %s" % _aud(abs(f)))
        if np.isfinite(sc or np.nan) and sc < -0.02:
            out.append("卖盘明显强于买盘")
        if np.isfinite(s4z or np.nan) and s4z < -0.8:
            out.append("放量下跌，像是在出货")
        if np.isfinite(r.get("ret_20d") or np.nan) and r["ret_20d"] < 0:
            out.append("近20天已经跌了 %.1f%%" % (abs(r["ret_20d"]) * 100))
    out = out[:3]
    note = profile_note(r.get("profile"))
    if note:
        out.append(note)
    sc = r.get("short_cost")
    if side == "out" and sc:
        from .shortcost import note as _sc_note
        n2 = _sc_note(sc)
        if n2:
            out.append(n2)
    return out
