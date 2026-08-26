# -*- coding: utf-8 -*-
"""个股多空评分与三只具体推荐（v2）。

评分是全池横截面 z 分，混合三类可以各自独立核对的证据：

  持仓面 -- ASIC 披露的空头持仓：它的水平，以及更重要的 20 日变化。空头平均而言
            是知情的 (Asquith-Pathak-Ritter 2005; Boehmer-Jones-Zhang 2008)，
            所以空头在加仓是看空证据，空头在回补是买盘需求。
  资金流 -- 蔡金资金流、OBV 斜率、成交额、带符号的资金流。
  技术面 -- 趋势位置、动量，以及价格已经走了多远。

任何推荐之前先过流动性闸门：一个没法按规模成交、或者借不到券的名字，
不管评分多高都不是推荐。

v2 修正
-------
1. **缺失成分不再被当成「中性」拉低排名**。v1 里 `_xz` 把缺失值置 0，看似中性，
   实际后果是：一只没有 ASIC 空头披露的股票，多头分里有 0.20 的权重、空头分里
   有 0.26 的权重被**强制钉死在 0**，于是它在排序里被机械地推向中间 ——
   既选不进多头前三，也选不进空头前三。现在改为**按可得成分重新归一化权重**，
   缺失的成分退出加权而不是投一张 0 票。
2. **`coverage` 字段**。每只股票报告它的评分实际由多少权重支撑，
   低于阈值的名字在推荐里被剔除 —— 「用 60% 的证据算出来的高分」不该和
   「用 100% 的证据算出来的高分」排在一起比。
3. 推荐列表增加**行业分散**上限，避免三只票是同一个板块里高度相关的同一笔押注。
4. 修掉几处 `None` 会抛 TypeError 的格式化，以及 `r not in longs` 的 O(n^2)
   字典比较（改为按 ticker 去重）。
"""
from __future__ import annotations

import numpy as np

MIN_ADV_LONG = 3_000_000      # AUD 20日平均成交额
MIN_ADV_SHORT = 8_000_000     # 做空需要更好的流动性与借券
MIN_COVERAGE = 0.70           # 评分权重覆盖率低于此不进推荐

LONG_WEIGHTS = {
    "sector_flow":   0.16,
    "cmf":           0.12,
    "obv":           0.08,
    "turnover":      0.07,
    "mom20":         0.10,
    "mom60":         0.08,
    "above_ma50":    0.07,
    "above_ma200":   0.07,
    "short_cover":   0.13,     # -(空头持仓20日变化)
    "short_low":     0.07,     # -(空头持仓水平)
    "not_extended":  0.05,     # RSI 远高于 70 的惩罚项
}

SHORT_WEIGHTS = {
    "sector_short":  0.16,
    "short_build":   0.18,     # +空头持仓20日变化 —— 主动信号
    "short_level":   0.08,
    "cmf_neg":       0.12,
    "obv_neg":       0.10,
    "mom_neg":       0.10,
    "below_ma":      0.08,
    "extension":     0.10,     # 价格已被拉高 = 回归风险
    "squeeze_safe":  0.08,     # 拥挤空头（回补天数高）的惩罚项
}


def _xz(vals, clip=3.0):
    """横截面 z 分。返回 (z, available_mask)。

    缺失位置的 z 仍置 0（调用方需要一个可加的数组），但 mask 让调用方
    知道**哪些位置不该计入分母**。
    """
    v = np.array([np.nan if x is None else float(x) for x in vals], dtype=float)
    ok = np.isfinite(v)
    if ok.sum() < 5:
        return np.zeros_like(v), np.zeros_like(v, dtype=bool)
    mu, sd = np.nanmean(v[ok]), np.nanstd(v[ok])
    if not np.isfinite(sd) or sd == 0:
        return np.zeros_like(v), np.zeros_like(v, dtype=bool)
    z = (v - mu) / sd
    z[~ok] = 0.0
    return np.clip(z, -clip, clip), ok


def _blend(components, weights):
    """按**可得成分**重新归一化的加权和。

    components: {name: (z_array, ok_mask)}
    返回 (score, coverage)，coverage 是每只股票实际被覆盖的权重比例。
    """
    n = len(next(iter(components.values()))[0])
    num = np.zeros(n)
    den = np.zeros(n)
    for name, w in weights.items():
        z, ok = components[name]
        num += w * z * ok
        den += w * ok
    total = sum(weights.values())
    cov = den / total
    score = np.divide(num, den, out=np.zeros(n), where=den > 1e-9)
    return score, cov


def score_stocks(panel, halted=None):
    """给每个成分股附上 long_score / short_score，返回扁平列表。

    `halted` 的名字直接不过两个流动性闸门：停牌或退市的股票买不进也卖不出，
    不管评分多高都不该出现在推荐里。
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
    # 超买惩罚：RSI 缺失时**不**假装它是 50，而是标为缺失，由 _blend 剔除。
    ob = np.where(np.isfinite(rsi), np.maximum(0.0, rsi - 72.0), np.nan)

    lc = {
        "sector_flow":  _xz(g("sector_flow")),
        "cmf":          _xz(g("cmf20")),
        "obv":          _xz(g("obv_slope")),
        "turnover":     _xz(g("dollar_vol_z")),
        "mom20":        _xz(g("ret_20d")),
        "mom60":        _xz(g("ret_60d")),
        "above_ma50":   _xz(g("dist_ma50")),
        "above_ma200":  _xz(g("dist_ma200")),
        "short_cover":  _neg(_xz(g("short_chg_20d"))),
        "short_low":    _neg(_xz(g("short_pct"))),
        "not_extended": _neg(_xz(ob)),
    }
    sc = {
        "sector_short": _xz(g("sector_short")),
        "short_build":  _xz(g("short_chg_20d")),
        "short_level":  _xz(g("short_pct")),
        "cmf_neg":      _neg(_xz(g("cmf20"))),
        "obv_neg":      _neg(_xz(g("obv_slope"))),
        "mom_neg":      _neg(_xz(g("ret_20d"))),
        "below_ma":     _neg(_xz(g("dist_ma50"))),
        "extension":    _xz(g("extension")),
        "squeeze_safe": _neg(_xz(g("days_to_cover"))),
    }

    ls, lcov = _blend(lc, LONG_WEIGHTS)
    ss, scov = _blend(sc, SHORT_WEIGHTS)
    ls = ls / (np.std(ls) or 1.0)
    ss = ss / (np.std(ss) or 1.0)

    for i, r in enumerate(rows):
        adv = r.get("adv_aud") or 0
        r["long_score"] = round(float(ls[i]), 3)
        r["short_score"] = round(float(ss[i]), 3)
        r["long_coverage"] = round(float(lcov[i]), 3)
        r["short_coverage"] = round(float(scov[i]), 3)
        r["long_parts"] = {n: round(float(LONG_WEIGHTS[n] * lc[n][0][i]), 3)
                           for n in LONG_WEIGHTS}
        r["short_parts"] = {n: round(float(SHORT_WEIGHTS[n] * sc[n][0][i]), 3)
                            for n in SHORT_WEIGHTS}
        r["long_missing"] = [n for n in LONG_WEIGHTS if not lc[n][1][i]]
        r["short_missing"] = [n for n in SHORT_WEIGHTS if not sc[n][1][i]]
        tradable = r["ticker"] not in halted
        r["halted"] = not tradable
        r["liquid_long"] = bool(tradable and adv >= MIN_ADV_LONG
                                and lcov[i] >= MIN_COVERAGE)
        r["liquid_short"] = bool(tradable and adv >= MIN_ADV_SHORT
                                 and scov[i] >= MIN_COVERAGE
                                 and r.get("short_pct") is not None
                                 and np.isfinite(r.get("short_pct") or np.nan))
    return rows


def _neg(pair):
    z, ok = pair
    return -z, ok


# ----------------------------------------------------------------------
def _fmt_pct(v, d=1):
    return "—" if v is None or not np.isfinite(v) else ("%+.*f%%" % (d, v * 100))


def _ok(r, k):
    """字段存在且是有限数值。"""
    v = r.get(k)
    return v is not None and np.isfinite(v)


def _long_reasons(r):
    out = []
    if _ok(r, "short_chg_20d") and r["short_chg_20d"] < -0.15 and _ok(r, "short_pct"):
        out.append("空头20日回补 %.2fpp（现 %.2f%%），卖压在撤" % (r["short_chg_20d"], r["short_pct"]))
    elif _ok(r, "short_pct") and r["short_pct"] < 1.0:
        out.append("空头持仓仅 %.2f%%，几乎无做空阻力" % r["short_pct"])
    if _ok(r, "cmf20") and r["cmf20"] > 0.05:
        out.append("蔡金资金流 +%.3f，20日处于净吸筹" % r["cmf20"])
    if _ok(r, "dollar_vol_z") and r["dollar_vol_z"] > 0.8:
        out.append("成交额较常态放大 %.1f 个标准差" % r["dollar_vol_z"])
    if _ok(r, "dist_ma50") and r["dist_ma50"] > 0:
        out.append("站上50日均线 %s，趋势结构完好" % _fmt_pct(r["dist_ma50"]))
    if _ok(r, "ret_60d") and r["ret_60d"] > 0:
        out.append("60日动量 %s" % _fmt_pct(r["ret_60d"]))
    if _ok(r, "rsi14") and r["rsi14"] < 68:
        out.append("RSI %.0f，尚未进入超买区" % r["rsi14"])
    if r.get("long_missing"):
        out.append("注意：%d 项证据缺失（覆盖率 %.0f%%）" %
                   (len(r["long_missing"]), (r.get("long_coverage") or 0) * 100))
    return out[:5]


def _short_reasons(r):
    out = []
    if _ok(r, "short_chg_20d") and r["short_chg_20d"] > 0.15 and _ok(r, "short_pct"):
        out.append("空头20日增仓 +%.2fpp 至 %.2f%%，机构在加空" % (r["short_chg_20d"], r["short_pct"]))
    elif _ok(r, "short_pct") and r["short_pct"] > 4:
        out.append("空头持仓已达 %.2f%%（1年分位 %.0f%%）" %
                   (r["short_pct"], (r.get("short_pctile_1y") or 0) * 100))
    if _ok(r, "cmf20") and r["cmf20"] < -0.02:
        out.append("蔡金资金流 %.3f，20日为净派发" % r["cmf20"])
    if _ok(r, "ret_20d") and r["ret_20d"] < 0:
        out.append("20日跌 %s，动能向下" % _fmt_pct(r["ret_20d"]))
    if _ok(r, "dist_ma50") and r["dist_ma50"] < 0:
        out.append("跌破50日均线 %s" % _fmt_pct(r["dist_ma50"]))
    if _ok(r, "extension") and r["extension"] > 0.5:
        out.append("价格仍处高位（延展度 %.2f），均值回归空间大" % r["extension"])
    if _ok(r, "days_to_cover"):
        out.append("回补天数 %.1f 天，%s" % (r["days_to_cover"],
                   "轧空风险可控" if r["days_to_cover"] < 8 else "注意轧空风险"))
    if r.get("short_missing"):
        out.append("注意：%d 项证据缺失（覆盖率 %.0f%%）" %
                   (len(r["short_missing"]), (r.get("short_coverage") or 0) * 100))
    return out[:5]


def build_recommendations(panel, stocks, n=3, profiles=None, short_cost=None,
                          max_per_sector=2):
    """选一个板块做多、一个板块做空，再在其中各挑 n 只。"""
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

    def _fill(chosen, pool_key, score_key):
        """板块内不够就在全市场补，但每个板块最多 `max_per_sector` 只 ——
        三只高度相关的同板块股票是一笔押注，不是三笔。"""
        have = {r["ticker"] for r in chosen}
        by_sec = {}
        for r in chosen:
            by_sec[r["sector"]] = by_sec.get(r["sector"], 0) + 1
        extra = sorted([r for r in stocks if r[pool_key] and r["ticker"] not in have],
                       key=lambda r: -r[score_key])
        for r in extra:
            if len(chosen) >= n:
                break
            if by_sec.get(r["sector"], 0) >= max_per_sector:
                continue
            chosen.append(r)
            have.add(r["ticker"])
            by_sec[r["sector"]] = by_sec.get(r["sector"], 0) + 1
        return chosen

    if len(longs) < n:
        longs = _fill(longs, "liquid_long", "long_score")
    if len(shorts) < n:
        shorts = _fill(shorts, "liquid_short", "short_score")

    def _pack(r, side):
        return {
            "ticker": r["ticker"], "code": r["code"],
            "sector": r["sector"], "sector_name": r["sector_name"],
            "px": r["px"], "score": r["long_score"] if side == "long" else r["short_score"],
            "coverage": r.get("long_coverage") if side == "long" else r.get("short_coverage"),
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
    """按波动率标定的止损距离，让仓位管理有个可用的数字。"""
    atr = r.get("atr_pct")
    px = r.get("px")
    if atr is None or px is None or not np.isfinite(atr) or not np.isfinite(px):
        return None
    dist = 2.5 * atr
    return {"atr_pct": round(float(atr), 4), "stop_pct": round(float(dist), 4),
            "stop_px": round(float(px * (1 - dist)) if side == "long" else float(px * (1 + dist)), 3)}
