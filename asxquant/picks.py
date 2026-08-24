# -*- coding: utf-8 -*-
"""Stock-level long / short scoring and the concrete three-name recommendations.

Scores are cross-sectional z-scores over the whole universe, blending three evidence
types the user can check independently:

  positioning -- disclosed ASIC short interest: its level, and more importantly its
                 20-day change. Short sellers are informed on average
                 (Asquith-Pathak-Ritter 2005; Boehmer-Jones-Zhang 2008), so shorts
                 building is bearish evidence and shorts covering is bullish demand.
  money flow  -- Chaikin money flow, OBV slope, turnover, signed dollar flow.
  technicals  -- trend location, momentum, and how extended the name already is.

A liquidity gate is applied before anything is recommended: a name that cannot be
traded in size, or borrowed, is not a recommendation regardless of its score.
"""
from __future__ import annotations

import numpy as np

MIN_ADV_LONG = 3_000_000      # AUD average daily turnover
MIN_ADV_SHORT = 8_000_000     # shorting needs more liquidity and borrow

LONG_WEIGHTS = {
    "sector_flow":   0.16,
    "cmf":           0.12,
    "obv":           0.08,
    "turnover":      0.07,
    "mom20":         0.10,
    "mom60":         0.08,
    "above_ma50":    0.07,
    "above_ma200":   0.07,
    "short_cover":   0.13,     # -(20d change in short interest)
    "short_low":     0.07,     # -(short interest level)
    "not_extended":  0.05,     # penalty for RSI far above 70
}

SHORT_WEIGHTS = {
    "sector_short":  0.16,
    "short_build":   0.18,     # +20d change in short interest -- the active signal
    "short_level":   0.08,
    "cmf_neg":       0.12,
    "obv_neg":       0.10,
    "mom_neg":       0.10,
    "below_ma":      0.08,
    "extension":     0.10,     # stretched price = reversion risk
    "squeeze_safe":  0.08,     # penalty for crowded shorts (high days-to-cover)
}


def _xz(vals, clip=3.0):
    v = np.array([np.nan if x is None else float(x) for x in vals], dtype=float)
    ok = np.isfinite(v)
    if ok.sum() < 5:
        return np.zeros_like(v)
    mu, sd = np.nanmean(v[ok]), np.nanstd(v[ok])
    if not np.isfinite(sd) or sd == 0:
        return np.zeros_like(v)
    z = (v - mu) / sd
    z[~ok] = 0.0
    return np.clip(z, -clip, clip)


def score_stocks(panel, halted=None):
    """Attach long_score / short_score to every constituent; return the flat list.

    `halted` names fail both liquidity gates outright: a suspended or delisted stock
    cannot be bought or sold, so it has no business in a recommendation list however
    well it scores.
    """
    halted = halted or set()
    rows = []
    for k, p in panel.items():
        for s in p["stocks"]:
            r = dict(s)
            r["sector"] = k
            r["sector_name"] = p["name"]
            r["sector_flow"] = p["flow"]["score"]
            r["sector_short"] = p["short"]["score"]
            r["sector_heat"] = p["heat"]["score"]
            r["sector_stage"] = p["stage"]["key"]
            r["sector_stage_label"] = p["stage"]["label"]
            rows.append(r)
    if not rows:
        return rows

    g = lambda f: [r.get(f) for r in rows]                       # noqa: E731
    rsi = np.array([r.get("rsi14") if r.get("rsi14") is not None else np.nan
                    for r in rows], dtype=float)

    lc = {
        "sector_flow":  _xz(g("sector_flow")),
        "cmf":          _xz(g("cmf20")),
        "obv":          _xz(g("obv_slope")),
        "turnover":     _xz(g("dollar_vol_z")),
        "mom20":        _xz(g("ret_20d")),
        "mom60":        _xz(g("ret_60d")),
        "above_ma50":   _xz(g("dist_ma50")),
        "above_ma200":  _xz(g("dist_ma200")),
        "short_cover":  -_xz(g("short_chg_20d")),
        "short_low":    -_xz(g("short_pct")),
        "not_extended": -_xz(np.maximum(0.0, np.nan_to_num(rsi, nan=50.0) - 72.0)),
    }
    sc = {
        "sector_short": _xz(g("sector_short")),
        "short_build":  _xz(g("short_chg_20d")),
        "short_level":  _xz(g("short_pct")),
        "cmf_neg":      -_xz(g("cmf20")),
        "obv_neg":      -_xz(g("obv_slope")),
        "mom_neg":      -_xz(g("ret_20d")),
        "below_ma":     -_xz(g("dist_ma50")),
        "extension":    _xz(g("extension")),
        "squeeze_safe": -_xz(g("days_to_cover")),
    }

    ls = sum(LONG_WEIGHTS[n] * lc[n] for n in LONG_WEIGHTS)
    ss = sum(SHORT_WEIGHTS[n] * sc[n] for n in SHORT_WEIGHTS)
    ls = ls / (np.std(ls) or 1.0)
    ss = ss / (np.std(ss) or 1.0)

    for i, r in enumerate(rows):
        adv = r.get("adv_aud") or 0
        r["long_score"] = round(float(ls[i]), 3)
        r["short_score"] = round(float(ss[i]), 3)
        r["long_parts"] = {n: round(float(LONG_WEIGHTS[n] * lc[n][i]), 3) for n in LONG_WEIGHTS}
        r["short_parts"] = {n: round(float(SHORT_WEIGHTS[n] * sc[n][i]), 3) for n in SHORT_WEIGHTS}
        tradable = r["ticker"] not in halted
        r["halted"] = not tradable
        r["liquid_long"] = bool(tradable and adv >= MIN_ADV_LONG)
        r["liquid_short"] = bool(tradable and adv >= MIN_ADV_SHORT
                                 and r.get("short_pct") is not None
                                 and np.isfinite(r.get("short_pct") or np.nan))
    return rows


# ----------------------------------------------------------------------
def _fmt_pct(v, d=1):
    return "—" if v is None or not np.isfinite(v) else ("%+.*f%%" % (d, v * 100))


def _long_reasons(r):
    out = []
    if r.get("short_chg_20d") is not None and np.isfinite(r["short_chg_20d"]) and r["short_chg_20d"] < -0.15:
        out.append("空头20日回补 %.2fpp（现 %.2f%%），卖压在撤" % (r["short_chg_20d"], r["short_pct"]))
    elif r.get("short_pct") is not None and np.isfinite(r["short_pct"]) and r["short_pct"] < 1.0:
        out.append("空头持仓仅 %.2f%%，几乎无做空阻力" % r["short_pct"])
    if r.get("cmf20") is not None and np.isfinite(r["cmf20"]) and r["cmf20"] > 0.05:
        out.append("蔡金资金流 +%.3f，20日处于净吸筹" % r["cmf20"])
    if r.get("dollar_vol_z") is not None and np.isfinite(r["dollar_vol_z"]) and r["dollar_vol_z"] > 0.8:
        out.append("成交额较常态放大 %.1f 个标准差" % r["dollar_vol_z"])
    if r.get("dist_ma50") is not None and np.isfinite(r["dist_ma50"]) and r["dist_ma50"] > 0:
        out.append("站上50日均线 %s，趋势结构完好" % _fmt_pct(r["dist_ma50"]))
    if r.get("ret_60d") is not None and np.isfinite(r["ret_60d"]) and r["ret_60d"] > 0:
        out.append("60日动量 %s" % _fmt_pct(r["ret_60d"]))
    if r.get("rsi14") is not None and np.isfinite(r["rsi14"]) and r["rsi14"] < 68:
        out.append("RSI %.0f，尚未进入超买区" % r["rsi14"])
    return out[:5]


def _short_reasons(r):
    out = []
    if r.get("short_chg_20d") is not None and np.isfinite(r["short_chg_20d"]) and r["short_chg_20d"] > 0.15:
        out.append("空头20日增仓 +%.2fpp 至 %.2f%%，机构在加空" % (r["short_chg_20d"], r["short_pct"]))
    elif r.get("short_pct") is not None and np.isfinite(r["short_pct"]) and r["short_pct"] > 4:
        out.append("空头持仓已达 %.2f%%（1年分位 %.0f%%）" %
                   (r["short_pct"], (r.get("short_pctile_1y") or 0) * 100))
    if r.get("cmf20") is not None and np.isfinite(r["cmf20"]) and r["cmf20"] < -0.02:
        out.append("蔡金资金流 %.3f，20日为净派发" % r["cmf20"])
    if r.get("ret_20d") is not None and np.isfinite(r["ret_20d"]) and r["ret_20d"] < 0:
        out.append("20日跌 %s，动能向下" % _fmt_pct(r["ret_20d"]))
    if r.get("dist_ma50") is not None and np.isfinite(r["dist_ma50"]) and r["dist_ma50"] < 0:
        out.append("跌破50日均线 %s" % _fmt_pct(r["dist_ma50"]))
    if r.get("extension") is not None and np.isfinite(r["extension"]) and r["extension"] > 0.5:
        out.append("价格仍处高位（延展度 %.2f），均值回归空间大" % r["extension"])
    if r.get("days_to_cover") is not None and np.isfinite(r["days_to_cover"]):
        out.append("回补天数 %.1f 天，%s" % (r["days_to_cover"],
                   "轧空风险可控" if r["days_to_cover"] < 8 else "注意轧空风险"))
    return out[:5]


def build_recommendations(panel, stocks, n=3, profiles=None, short_cost=None):
    """Pick one sector to be long and one to be short, then n names inside each."""
    if not panel or not stocks:
        return None

    stage_bonus_long = {"early_in": 0.55, "crowded_in": -0.35, "neutral": 0.0,
                        "extended_flat": -0.1, "outflow": -0.3, "washout": -0.45,
                        "distribution": -0.6}
    stage_bonus_short = {"distribution": 0.60, "crowded_in": 0.20, "outflow": 0.15,
                         "washout": 0.05, "extended_flat": 0.10, "neutral": 0.0,
                         "early_in": -0.55}

    lk = max(panel, key=lambda k: panel[k]["flow"]["score"]
             + stage_bonus_long.get(panel[k]["stage"]["key"], 0.0))
    sk = max(panel, key=lambda k: panel[k]["short"]["score"]
             + stage_bonus_short.get(panel[k]["stage"]["key"], 0.0))

    longs = sorted([r for r in stocks if r["sector"] == lk and r["liquid_long"]],
                   key=lambda r: -r["long_score"])[:n]
    shorts = sorted([r for r in stocks if r["sector"] == sk and r["liquid_short"]],
                    key=lambda r: -r["short_score"])[:n]

    # fall back to the whole market if a sector is too thin to fill the slate
    if len(longs) < n:
        extra = sorted([r for r in stocks if r["liquid_long"] and r not in longs],
                       key=lambda r: -r["long_score"])
        longs += extra[:n - len(longs)]
    if len(shorts) < n:
        extra = sorted([r for r in stocks if r["liquid_short"] and r not in shorts],
                       key=lambda r: -r["short_score"])
        shorts += extra[:n - len(shorts)]

    def _pack(r, side):
        return {
            "ticker": r["ticker"], "code": r["code"],
            "sector": r["sector"], "sector_name": r["sector_name"],
            "px": r["px"], "score": r["long_score"] if side == "long" else r["short_score"],
            "ret_20d": r["ret_20d"], "ret_60d": r["ret_60d"], "rsi14": r["rsi14"],
            "cmf20": r["cmf20"], "dollar_vol_z": r["dollar_vol_z"],
            "short_pct": r["short_pct"], "short_chg_20d": r["short_chg_20d"],
            "short_pctile_1y": r["short_pctile_1y"], "days_to_cover": r["days_to_cover"],
            "dist_ma50": r["dist_ma50"], "dist_ma200": r["dist_ma200"],
            "adv_aud": r["adv_aud"], "vol20": r["vol20"], "atr_pct": r["atr_pct"],
            "extension": r["extension"],
            "profile": (profiles or {}).get(r["ticker"]),
            "short_cost": (short_cost or {}).get(r["code"]),
            "reasons": _long_reasons(r) if side == "long" else _short_reasons(r),
            "parts": r["long_parts"] if side == "long" else r["short_parts"],
            "stop_hint": _stop(r, side),
        }

    lp, sp = panel[lk], panel[sk]
    return {
        "long": {
            "sector": lk, "sector_name": lp["name"],
            "flow_score": lp["flow"]["score"], "heat_score": lp["heat"]["score"],
            "stage": lp["stage"]["label"], "stage_note": lp["stage"]["note"],
            "quadrant": lp["rotation"]["quadrant_cn"],
            "ret_20d": lp["perf"]["ret_20d"],
            "short_pct": lp["raw"]["short_pct"],
            "picks": [_pack(r, "long") for r in longs],
        },
        "short": {
            "sector": sk, "sector_name": sp["name"],
            "short_score": sp["short"]["score"], "flow_score": sp["flow"]["score"],
            "heat_score": sp["heat"]["score"],
            "stage": sp["stage"]["label"], "stage_note": sp["stage"]["note"],
            "quadrant": sp["rotation"]["quadrant_cn"],
            "ret_20d": sp["perf"]["ret_20d"],
            "short_pct": sp["raw"]["short_pct"], "short_chg_20d": sp["raw"]["short_chg_20d"],
            "picks": [_pack(r, "short") for r in shorts],
        },
    }


def _stop(r, side):
    """A volatility-scaled stop distance, so position sizing has a number to work from."""
    atr = r.get("atr_pct")
    px = r.get("px")
    if atr is None or px is None or not np.isfinite(atr) or not np.isfinite(px):
        return None
    dist = 2.5 * atr
    return {"atr_pct": round(float(atr), 4), "stop_pct": round(float(dist), 4),
            "stop_px": round(float(px * (1 - dist)) if side == "long" else float(px * (1 + dist)), 3)}
