
# -*- coding: utf-8 -*-
"""Sector construction, money flow, speculative heat, and short-side positioning.

Four distinct questions are answered with four distinct scores, because conflating
them is what makes most "sector rotation" dashboards useless:

  flow     -- is money arriving or leaving?
  heat     -- is this sector being speculated on right now (turnover, acceleration,
              volatility, overbought breadth)? Heat is not the same as flow: a sector
              can be hot on churn while smart money leaves.
  extension-- how far has price already travelled? Combined with flow this separates
              "money is starting to arrive" from "money already arrived and is crowded".
  short    -- where is disclosed short interest concentrated and building?
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind
from .config import BENCHMARK, SECTORS

FLOW_WEIGHTS = {
    "cmf_z":        0.18,   # Chaikin money flow
    "signed_z":     0.16,   # signed dollar flow, 20d, scaled by sector cap
    "obv_z":        0.13,   # on-balance-volume slope
    "dollarvol_z":  0.11,   # turnover vs own 60d norm
    "mfi_z":        0.10,   # money flow index
    "rs_ratio_z":   0.13,   # RRG relative-strength trend
    "rs_mom_z":     0.12,   # RRG relative-strength momentum
    "shortcover_z": 0.07,   # short interest falling = covering = demand
}

HEAT_WEIGHTS = {
    "turnover_z":   0.22,   # turnover surge vs own history
    "accel_z":      0.20,   # price acceleration (5d vs 20d pace)
    "volexp_z":     0.16,   # realised volatility expansion
    "overbought_z": 0.16,   # share of constituents with RSI > 70
    "hi52_z":       0.14,   # share of constituents near 52-week highs
    "range_z":      0.12,   # ATR as a share of price, vs own history
}

SHORT_WEIGHTS = {
    "level_z":      0.22,   # disclosed short interest level
    "build_z":      0.26,   # short interest change over 20d -- the active signal
    "flow_neg_z":   0.16,   # money leaving
    "breadth_neg_z": 0.14,  # internals deteriorating
    "rs_neg_z":     0.12,   # relative-strength breakdown
    "extension_z":  0.10,   # stretched price = mean-reversion risk
}


# ----------------------------------------------------------------------
def infer_market_caps(closes, short_pct, short_shares):
    """shares on issue = short shares / short %, then cap = shares x last price."""
    caps = {}
    if short_pct is None or short_shares is None:
        return caps
    for t in closes.columns:
        if t not in short_pct.columns or t not in short_shares.columns:
            continue
        p = short_pct[t].dropna()
        s = short_shares[t].dropna()
        px = closes[t].dropna()
        if len(p) == 0 or len(s) == 0 or len(px) == 0:
            continue
        idx = p.index.intersection(s.index)
        if len(idx) == 0:
            continue
        p2, s2 = p.reindex(idx).tail(20), s.reindex(idx).tail(20)
        m = (p2 > 0.02) & (s2 > 0)
        if m.sum() < 3:
            continue
        shares_out = float(np.median((s2[m] / (p2[m] / 100.0)).values))
        if np.isfinite(shares_out) and shares_out > 0:
            caps[t] = shares_out * float(px.iloc[-1])
    return caps


def _weights(tickers, caps, closes, cap_at=0.25):
    avail = [t for t in tickers if t in closes.columns and closes[t].notna().sum() > 200]
    if not avail:
        return {}
    vals = [caps[t] for t in avail if t in caps and np.isfinite(caps[t]) and caps[t] > 0]
    if len(vals) < max(2, len(avail) // 3):
        return {t: 1.0 / len(avail) for t in avail}
    med = float(np.median(vals))
    w = {t: (caps[t] if (t in caps and np.isfinite(caps.get(t, np.nan)) and caps[t] > 0) else med)
         for t in avail}
    tot = sum(w.values())
    w = {t: v / tot for t, v in w.items()}
    for _ in range(8):
        over = {t: v for t, v in w.items() if v > cap_at}
        if not over:
            break
        excess = sum(v - cap_at for v in over.values())
        rest = {t: v for t, v in w.items() if v <= cap_at}
        rtot = sum(rest.values()) or 1.0
        for t in over:
            w[t] = cap_at
        for t in rest:
            w[t] += excess * rest[t] / rtot
    return w


def _wmean(vals, wts):
    v = np.array(vals, dtype=float)
    w = np.array(wts, dtype=float)
    m = np.isfinite(v) & np.isfinite(w) & (w > 0)
    if not m.any():
        return np.nan
    return float((v[m] * w[m]).sum() / w[m].sum())


# ----------------------------------------------------------------------
def build_sector_panel(px, short_pct, short_shares):
    closes, highs, lows, vols = px["close"], px["high"], px["low"], px["volume"]
    adj = px.get("adjclose", closes)
    caps = infer_market_caps(closes, short_pct, short_shares)
    bench = closes[BENCHMARK].dropna() if BENCHMARK in closes.columns else None

    out = {}
    for key, meta in SECTORS.items():
        w = _weights(meta["tickers"], caps, closes)
        if not w:
            continue
        tk = list(w.keys())
        wser = pd.Series(w)

        rets = adj[tk].pct_change()
        rw = rets.notna().mul(wser, axis=1)
        rw = rw.div(rw.sum(axis=1).replace(0, np.nan), axis=0)
        sec_ret = (rets.fillna(0) * rw).sum(axis=1).where(rw.sum(axis=1) > 0)
        sec_idx = (1 + sec_ret.fillna(0)).cumprod() * 1000.0

        stats = []
        sec_signed = pd.Series(0.0, index=closes.index)
        sec_dv = pd.Series(0.0, index=closes.index)
        sec_cap = 0.0
        wl = []

        for t in tk:
            c, h, l, v = closes[t], highs[t], lows[t], vols[t]
            if c.notna().sum() < 200:
                continue
            # Australian yields run 4-6% and are mostly paid in two lumps, so an
            # unadjusted series shows a fake gap down on every ex-dividend date.
            # Anything measured ACROSS days uses the adjusted series; anything
            # measured WITHIN a day (money flow, ATR, turnover) uses the raw bars,
            # which are internally consistent.
            ac = adj[t] if t in adj.columns else c
            cap = caps.get(t, np.nan)
            sec_cap += cap if np.isfinite(cap) else 0.0

            c_cmf = ind.cmf(h, l, c, v, 20)
            c_mfi = ind.mfi(h, l, c, v, 14)
            o = ind.obv(c, v)
            o_slope = (o - o.shift(20)) / v.rolling(60, min_periods=20).mean().replace(0, np.nan) / 20.0
            dv = ind.dollar_volume(c, v)
            dv_z = ind.zscore(dv.rolling(5, min_periods=2).mean(), 120)
            rsi = ind.rsi(ac, 14)
            atrp = ind.atr(h, l, c, 14) / c

            sec_signed = sec_signed.add(
                ind.signed_dollar_flow(h, l, c, v).reindex(closes.index).fillna(0), fill_value=0)
            sec_dv = sec_dv.add(dv.reindex(closes.index).fillna(0), fill_value=0)

            sp = short_pct[t] if (short_pct is not None and t in short_pct.columns) else None
            ss = short_shares[t] if (short_shares is not None and t in short_shares.columns) else None
            s_now = s_c20 = s_c5 = s_pctile = dtc = np.nan
            if sp is not None:
                spv = sp.dropna()
                if len(spv):
                    s_now = float(spv.iloc[-1])
                    if len(spv) > 21:
                        s_c20 = float(spv.iloc[-1] - spv.iloc[-21])
                    if len(spv) > 6:
                        s_c5 = float(spv.iloc[-1] - spv.iloc[-6])
                    tail = spv.tail(252)
                    if len(tail) > 40:
                        s_pctile = float((tail < s_now).mean())
            if ss is not None:
                ssv = ss.dropna()
                advol = v.rolling(20, min_periods=5).mean()
                if len(ssv) and np.isfinite(ind.safe_last(advol)) and ind.safe_last(advol) > 0:
                    dtc = float(ssv.iloc[-1]) / float(ind.safe_last(advol))

            ext = _extension_stock(ac, rsi)
            stats.append({
                "ticker": t, "code": t.replace(".AX", ""),
                "cap_aud": cap if np.isfinite(cap) else None,
                "weight": round(w[t], 4),
                "px": ind.safe_last(c),
                "ret_1d": ind.safe_last(ac.pct_change()),
                "ret_5d": ind.safe_last(ind.roc(ac, 5)),
                "ret_20d": ind.safe_last(ind.roc(ac, 20)),
                "ret_60d": ind.safe_last(ind.roc(ac, 60)),
                "rsi14": ind.safe_last(rsi),
                "cmf20": ind.safe_last(c_cmf),
                "mfi14": ind.safe_last(c_mfi),
                "obv_slope": ind.safe_last(o_slope),
                "dollar_vol_z": ind.safe_last(dv_z),
                "adv_aud": ind.safe_last(dv.rolling(20, min_periods=5).mean()),
                "dist_ma50": ind.safe_last(ind.dist_from_ma(ac, 50)),
                "dist_ma200": ind.safe_last(ind.dist_from_ma(ac, 200)),
                "pos_52w": ind.safe_last(ind.donchian_pos(ac, 252)),
                "atr_pct": ind.safe_last(atrp),
                "vol20": ind.safe_last(ind.realized_vol(ac, 20)),
                "short_pct": s_now, "short_chg_20d": s_c20, "short_chg_5d": s_c5,
                "short_pctile_1y": s_pctile, "days_to_cover": dtc,
                "extension": ext,
            })
            wl.append(w[t])

        if not stats:
            continue

        sc = adj[tk] if all(t in adj.columns for t in tk) else closes[tk]
        sv = vols[tk]
        pa50 = ind.pct_above_ma(sc, 50)
        pa200 = ind.pct_above_ma(sc, 200)
        adr = ind.ad_ratio(sc)

        rs_ratio = rs_mom = pd.Series(dtype=float)
        if bench is not None:
            b = bench.reindex(sec_idx.index).ffill()
            rs_ratio, rs_mom = ind.rrg(sec_idx, b, win=126, smooth=10, mom_lag=10)

        wts = [s["weight"] for s in stats]
        rsis = np.array([s["rsi14"] for s in stats], dtype=float)
        p52 = np.array([s["pos_52w"] for s in stats], dtype=float)

        # ---- heat inputs ----
        dv_sec = sec_dv.replace(0, np.nan)
        turnover_z = ind.safe_last(ind.zscore(dv_sec.rolling(5, min_periods=2).mean(), 250))
        r5 = ind.safe_last(sec_idx / sec_idx.shift(5) - 1)
        r20 = ind.safe_last(sec_idx / sec_idx.shift(20) - 1)
        accel = (r5 / 5.0) - (r20 / 20.0) if (np.isfinite(r5) and np.isfinite(r20)) else np.nan
        rv20 = ind.realized_vol(sec_idx, 20)
        rv60 = ind.realized_vol(sec_idx, 60)
        volexp = ind.safe_last(rv20) / (ind.safe_last(rv60) or np.nan) - 1.0
        # Mask before comparing: NaN > 70 is False, so an unmasked mean would count
        # names with missing data as "not overbought" and quietly deflate the ratio.
        _ok_r, _ok_p = np.isfinite(rsis), np.isfinite(p52)
        overbought = float((rsis[_ok_r] > 70).mean()) if _ok_r.any() else np.nan
        near_hi = float((p52[_ok_p] > 0.90).mean()) if _ok_p.any() else np.nan
        atr_sec = _wmean([s["atr_pct"] for s in stats], wts)

        out[key] = {
            "key": key, "name": meta["name"], "en": meta["en"], "n": len(stats),
            "cap_aud": sec_cap if sec_cap > 0 else None,
            "index": sec_idx, "ret": sec_ret, "stocks": stats,
            "breadth": {
                "pct_above_ma50": ind.safe_last(pa50),
                "pct_above_ma200": ind.safe_last(pa200),
                # safe_last(x) - safe_last(x.shift(20)) 会两次 dropna，尾部只要有一个 NaN
                # 取到的就不是相隔 20 根的两个点。lagged_diff 按位置取，不会错位。
                "pct_above_ma50_chg20": ind.lagged_diff(pa50, 20),
                "ad_ratio_5d": ind.safe_last(adr.rolling(5).mean()),
                "pct_overbought": overbought,
                "pct_near_52w_high": near_hi,
            },
            "raw": {
                "cmf": _wmean([s["cmf20"] for s in stats], wts),
                "mfi": _wmean([s["mfi14"] for s in stats], wts),
                "obv_slope": _wmean([s["obv_slope"] for s in stats], wts),
                "dollarvol_z": _wmean([s["dollar_vol_z"] for s in stats], wts),
                "rs_ratio": ind.safe_last(rs_ratio),
                "rs_mom": ind.safe_last(rs_mom),
                "signed_flow_20d": float(sec_signed.tail(20).sum()),
                "signed_flow_5d": float(sec_signed.tail(5).sum()),
                "turnover_z": turnover_z,
                "accel": accel,
                "volexp": volexp,
                "atr_pct": atr_sec,
                "short_pct": _wmean([s["short_pct"] for s in stats], wts),
                "short_chg_20d": _wmean([s["short_chg_20d"] for s in stats], wts),
                "short_chg_5d": _wmean([s["short_chg_5d"] for s in stats], wts),
                "short_pctile_1y": _wmean([s["short_pctile_1y"] for s in stats], wts),
                "extension": _wmean([s["extension"] for s in stats], wts),
            },
            "series": {
                "signed_flow": sec_signed, "rs_ratio": rs_ratio, "rs_mom": rs_mom,
                "pct_above_ma50": pa50, "index": sec_idx,
                "short_pct": (short_pct[tk].mul(wser[tk], axis=1).sum(axis=1, min_count=1)
                              / wser[tk].sum()) if short_pct is not None else pd.Series(dtype=float),
            },
            "perf": {
                "ret_1d": ind.safe_last(sec_ret),
                "ret_5d": r5, "ret_20d": r20,
                "ret_60d": ind.safe_last(sec_idx / sec_idx.shift(60) - 1),
                "ret_120d": ind.safe_last(sec_idx / sec_idx.shift(120) - 1),
                "vol_20d": ind.safe_last(rv20),
                "dist_ma200": ind.safe_last(ind.dist_from_ma(sec_idx, 200)),
                "pos_52w": ind.safe_last(ind.donchian_pos(sec_idx, 252)),
            },
        }
    return out


def _extension_stock(close, rsi):
    """How far a name has already travelled: blend of 52w position, MA200 gap, RSI."""
    p = ind.safe_last(ind.donchian_pos(close, 252))
    d = ind.safe_last(ind.dist_from_ma(close, 200))
    r = ind.safe_last(rsi)
    parts = []
    if np.isfinite(p):
        parts.append((p - 0.5) * 2)
    if np.isfinite(d):
        parts.append(np.clip(d / 0.20, -2, 2))
    if np.isfinite(r):
        parts.append((r - 50) / 20.0)
    return float(np.mean(parts)) if parts else np.nan


def _xz(values, clip=3.0):
    v = np.array([np.nan if x is None else float(x) for x in values], dtype=float)
    ok = np.isfinite(v)
    if ok.sum() < 3:
        return np.zeros_like(v)
    mu, sd = np.nanmean(v[ok]), np.nanstd(v[ok])
    if not np.isfinite(sd) or sd == 0:
        return np.zeros_like(v)
    z = (v - mu) / sd
    z[~ok] = 0.0
    return np.clip(z, -clip, clip)


def _norm(x):
    sd = np.std(x)
    return x / sd if sd > 1e-9 else x


# ----------------------------------------------------------------------
def score_sectors(panel):
    keys = list(panel.keys())
    if not keys:
        return panel

    caps = np.array([panel[k]["cap_aud"] or np.nan for k in keys], dtype=float)
    medc = np.nanmedian(caps[np.isfinite(caps)]) if np.isfinite(caps).any() else 1.0
    caps = np.where(np.isfinite(caps) & (caps > 0), caps, medc)
    signed20 = np.array([panel[k]["raw"]["signed_flow_20d"] for k in keys], dtype=float) / caps * 1e4

    R = lambda f: [panel[k]["raw"][f] for k in keys]          # noqa: E731
    B = lambda f: [panel[k]["breadth"][f] for k in keys]      # noqa: E731
    P = lambda f: [panel[k]["perf"][f] for k in keys]         # noqa: E731

    # ---------- flow ----------
    fcomp = {
        "cmf_z": _xz(R("cmf")), "signed_z": _xz(signed20), "obv_z": _xz(R("obv_slope")),
        "dollarvol_z": _xz(R("dollarvol_z")), "mfi_z": _xz(R("mfi")),
        "rs_ratio_z": _xz(R("rs_ratio")), "rs_mom_z": _xz(R("rs_mom")),
        "shortcover_z": -_xz(R("short_chg_20d")),
    }
    flow = _norm(sum(FLOW_WEIGHTS[n] * fcomp[n] for n in FLOW_WEIGHTS))

    # ---------- heat ----------
    hcomp = {
        "turnover_z": _xz(R("turnover_z")), "accel_z": _xz(R("accel")),
        "volexp_z": _xz(R("volexp")), "overbought_z": _xz(B("pct_overbought")),
        "hi52_z": _xz(B("pct_near_52w_high")), "range_z": _xz(R("atr_pct")),
    }
    heat = _norm(sum(HEAT_WEIGHTS[n] * hcomp[n] for n in HEAT_WEIGHTS))

    # ---------- extension ----------
    ext = _norm(0.4 * _xz(R("extension")) + 0.3 * _xz(P("dist_ma200")) +
                0.3 * _xz(P("pos_52w")))

    # ---------- short pressure ----------
    scomp = {
        "level_z": _xz(R("short_pct")), "build_z": _xz(R("short_chg_20d")),
        "flow_neg_z": -flow, "breadth_neg_z": -_xz(B("pct_above_ma50_chg20")),
        "rs_neg_z": -_xz(R("rs_ratio")), "extension_z": _xz(list(ext)),
    }
    short = _norm(sum(SHORT_WEIGHTS[n] * scomp[n] for n in SHORT_WEIGHTS))

    for i, k in enumerate(keys):
        f, hv, e, s = float(flow[i]), float(heat[i]), float(ext[i]), float(short[i])
        stage, stage_cn, stage_note = _stage(f, e)
        lab, cls = _flow_label(f)
        q, qn = ind.rrg_quadrant(panel[k]["raw"]["rs_ratio"], panel[k]["raw"]["rs_mom"])
        panel[k]["flow"] = {
            "score": round(f, 3), "label": lab, "cls": cls,
            "components": {n: round(float(fcomp[n][i]), 3) for n in fcomp},
            "contributions": {n: round(float(FLOW_WEIGHTS[n] * fcomp[n][i]), 3) for n in FLOW_WEIGHTS},
        }
        panel[k]["heat"] = {
            "score": round(hv, 3),
            "label": "极热" if hv >= 1.2 else "偏热" if hv >= 0.5 else
                     "温和" if hv > -0.5 else "冷清",
            "cls": "blaze" if hv >= 1.2 else "hot" if hv >= 0.5 else
                   "warm" if hv > -0.5 else "cold",
            "components": {n: round(float(hcomp[n][i]), 3) for n in hcomp},
        }
        panel[k]["extension"] = {"score": round(e, 3)}
        panel[k]["short"] = {
            "score": round(s, 3),
            "label": "高做空压力" if s >= 1.0 else "偏空" if s >= 0.35 else
                     "中性" if s > -0.5 else "空头稀少",
            "cls": "high" if s >= 1.0 else "mid" if s >= 0.35 else
                   "neutral" if s > -0.5 else "low",
            "components": {n: round(float(scomp[n][i]), 3) for n in scomp},
        }
        panel[k]["stage"] = {"key": stage, "label": stage_cn, "note": stage_note}
        panel[k]["rotation"] = {"quadrant": q, "quadrant_cn": qn}

    for r, k in enumerate(sorted(keys, key=lambda k: -panel[k]["flow"]["score"]), 1):
        panel[k]["flow"]["rank"] = r
    for r, k in enumerate(sorted(keys, key=lambda k: -panel[k]["heat"]["score"]), 1):
        panel[k]["heat"]["rank"] = r
    for r, k in enumerate(sorted(keys, key=lambda k: -panel[k]["short"]["score"]), 1):
        panel[k]["short"]["rank"] = r
    return panel


def _flow_label(f):
    if f >= 1.0:
        return "强力流入", "strong-in"
    if f >= 0.3:
        return "温和流入", "in"
    if f > -0.3:
        return "中性", "neutral"
    if f > -1.0:
        return "温和流出", "out"
    return "资金出逃", "strong-out"


def _stage(flow, ext):
    """The flow x extension map -- the difference between early and crowded."""
    if flow >= 0.3 and ext >= 0.6:
        return ("crowded_in", "资金已大幅流入 · 拥挤",
                "钱已经到位且价格走高，动能仍在但风险回报变差，追高需要更严的止损。")
    if flow >= 0.3:
        return ("early_in", "资金迹象流入 · 早期",
                "资金开始进场而价格尚未大幅拉升，这是风险回报最好的做多位置。")
    if flow <= -0.3 and ext >= 0.6:
        return ("distribution", "涨高但资金流出 · 派发",
                "价格仍在高位但资金在撤，典型的顶部派发形态，是做空的首选背景。")
    if flow <= -0.3 and ext <= -0.6:
        return ("washout", "跌深且资金流出 · 超跌",
                "资金持续离场且价格已深跌，下跌动能仍在，抄底缺乏资金面依据。")
    if flow <= -0.3:
        return ("outflow", "资金流出", "资金净流出，缺乏做多依据。")
    if ext >= 0.6:
        return ("extended_flat", "价格偏高 · 资金中性",
                "价格已高但资金既未大举进场也未撤离，方向未定。")
    return ("neutral", "中性", "资金与价格均无明显偏向。")
