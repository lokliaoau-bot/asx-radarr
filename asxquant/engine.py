# -*- coding: utf-8 -*-
"""Orchestration: pull data, fit every target, score sectors and stocks, build the report.

Layers, ordered by how much the measured evidence actually supports them:

  1. Sector flow / heat / short pressure -- measurement. Disclosed ASIC positioning
     plus price-volume flow. No model required.
  2. Stock long/short ranking -- validated cross-sectionally in `validate.py`. The
     short side carries real, significant information; the long side does not.
  3. Index direction and risk -- forecast, and mostly weak. Probabilities are shrunk
     toward the base rate by measured out-of-sample discrimination.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import json
import math
import time
import traceback

import numpy as np
import pandas as pd

from . import announcements as AN
from . import archive as AR
from . import datafeed as D
from . import features as F
from . import indicators as I
from . import model as M
from . import picks as PK
from . import sectors as S
from . import shortcost as SC
from . import smartmoney as SM
from . import validate as V
from .config import (BENCHMARK, MACRO, MODEL_VERSION, SECTORS, all_stock_tickers,
                     ticker_to_sector)

MIN_TRAIN = 750
FULL_SKILL_AUC = 0.62
RECENT_YEARS = 3


def _targets(bench):
    out = []
    for h in (1, 5, 10, 20):
        fwd = bench.shift(-h) / bench - 1.0
        out.append({"key": "dir_%dd" % h, "group": "direction", "horizon": h,
                    "name": "未来%d个交易日上涨" % h, "short": "%d日方向" % h,
                    "y": (fwd > 0).astype(float).where(fwd.notna()), "fwd": fwd})
    rv = I.realized_vol(bench, 20)
    frv = rv.shift(-20)
    out.append({"key": "vol_up_20d", "group": "risk", "horizon": 20,
                "name": "未来20日波动率上升", "short": "波动率上行",
                "y": (frv > rv).astype(float).where(frv.notna()), "fwd": frv / rv - 1.0})
    for h, thr in ((10, -0.04), (20, -0.05), (20, -0.08)):
        fmin = bench[::-1].rolling(h, min_periods=h).min()[::-1].shift(-1)
        dd = fmin / bench - 1.0
        out.append({"key": "dd_%d_%dd" % (int(abs(thr) * 100), h), "group": "risk", "horizon": h,
                    "name": "未来%d日内最大回撤超过%d%%" % (h, int(abs(thr) * 100)),
                    "short": "%d日回撤>%d%%" % (h, int(abs(thr) * 100)),
                    "y": (dd < thr).astype(float).where(dd.notna()), "fwd": dd})
    return out


def _skill_verdict(auc, bss, n):
    if auc is None or n is None or n < 200:
        return "insufficient", "样本不足"
    if auc >= 0.60 and (bss or 0) > 0:
        return "strong", "显著预测力"
    if auc >= 0.55 and (bss or 0) > -0.01:
        return "moderate", "中等预测力"
    if auc >= 0.52:
        return "weak", "微弱预测力"
    return "none", "无统计显著预测力"


def _fit_target(tg, Xf, cache=None, log=print):
    h = tg["horizon"]
    Xa, ya = Xf.align(tg["y"], join="inner", axis=0)
    prev = (cache or {}).get(tg["key"]) or {}
    P = M.walk_forward(Xa, ya, horizon=h, min_train=MIN_TRAIN,
                       refit_every=M.REFIT_EVERY, cached=prev.get("preds"))
    ens = P["ensemble"]
    cal = M.calibrate_expanding(ens, ya, h, cached=prev.get("cal"))

    m_raw, m_cal = M.evaluate(ens, ya), M.evaluate(cal, ya)
    metrics = m_cal or m_raw
    series = cal if m_cal is not None else ens
    s = series.dropna()
    if len(s) == 0:
        return None

    cutoff = s.index.max() - pd.Timedelta(days=365 * RECENT_YEARS)
    m_recent = M.evaluate(series[series.index >= cutoff], ya[ya.index >= cutoff])

    p_model = float(s.iloc[-1])
    base = metrics["base_rate"] if metrics else 0.5
    aucs = [a for a in [(metrics or {}).get("auc"), (m_recent or {}).get("auc")] if a is not None]
    p_final, lam = M.shrink_to_base(p_model, base, min(aucs) if aucs else None,
                                    full_skill_auc=FULL_SKILL_AUC)
    lvl, lvl_cn = _skill_verdict((metrics or {}).get("auc"),
                                 (metrics or {}).get("brier_skill_score"),
                                 (metrics or {}).get("n"))
    hist = s.tail(260)
    payload = {
        "key": tg["key"], "group": tg["group"], "name": tg["name"], "short": tg["short"],
        "horizon": h, "p_model": round(p_model, 4),
        "p_final": round(float(np.clip(p_final, 0.01, 0.99)), 4),
        "base_rate": round(float(base), 4), "shrink_lambda": round(float(lam), 3),
        "edge_pp": round(float((p_final - base) * 100), 2),
        "metrics": metrics, "metrics_recent": m_recent,
        "skill": lvl, "skill_cn": lvl_cn,
        "conditional": M.conditional_outcomes(series, tg["fwd"].reindex(series.index), p_model),
        "history": {"dates": [str(x.date()) for x in hist.index],
                    "p": [round(float(v), 4) for v in hist.values]},
    }
    return payload, {"preds": P[["logit", "gbm", "combo"]], "cal": cal}


def _direction_summary(results):
    dirs = [r for r in results if r["group"] == "direction"]
    if not dirs:
        return None
    wsum = sum(r["shrink_lambda"] for r in dirs)
    tilt = (sum(r["shrink_lambda"] * (r["p_final"] - 0.5) for r in dirs) / wsum) if wsum > 1e-9 else 0.0
    p = 0.5 + tilt
    if wsum < 0.15:
        stance, cls = "无方向性优势 — 建议中性", "neutral"
    elif p >= 0.56:
        stance, cls = "偏多", "bull"
    elif p >= 0.52:
        stance, cls = "轻微偏多", "mild-bull"
    elif p > 0.48:
        stance, cls = "中性", "neutral"
    elif p > 0.44:
        stance, cls = "轻微偏空", "mild-bear"
    else:
        stance, cls = "偏空", "bear"
    return {"p_up": round(float(p), 4), "stance": stance, "cls": cls,
            "confidence": round(float(np.clip(wsum / max(len(dirs), 1), 0, 1)), 3)}


def _macro_snapshot(px):
    closes = px["close"]
    out = []
    for tk, name in MACRO.items():
        if tk not in closes.columns:
            continue
        s = closes[tk].dropna()
        if len(s) < 25:
            continue
        out.append({"ticker": tk, "name": name, "last": round(float(s.iloc[-1]), 4),
                    "chg_1d": round(float(s.iloc[-1] / s.iloc[-2] - 1), 5),
                    "chg_5d": round(float(s.iloc[-1] / s.iloc[-6] - 1), 5),
                    "chg_20d": round(float(s.iloc[-1] / s.iloc[-21] - 1), 5)})
    return out


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 6)
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, dt.date, dt.datetime)):
        return str(o)
    return o


# ----------------------------------------------------------------------
def run(force=False, log=print, progress=None):
    t_all = time.time()

    def step(msg, pct):
        log(msg)
        if progress:
            progress(msg, pct)

    step("正在获取行情数据 ...", 5)
    px = D.fetch_prices(force=force, log=log)
    step("正在获取 ASIC 空头持仓 ...", 16)
    shorts = D.fetch_shorts(force=force, log=log)

    bench = px["close"][BENCHMARK]
    bd = bench.dropna()
    asof = px["close"].index[-1]
    tickers = [t for t in all_stock_tickers() if t in px["close"].columns]
    spct, ssha = D.shorts_panel(shorts, tickers, px["close"].index)

    # Suspended / delisted names keep their last close forever. Yahoo already NaNs
    # them out (so the backtest masks them correctly), but the live cards read the
    # last VALID close and would quote a dead price as today's. Detect and gate.
    halted = D.halted_tickers(px, tickers)
    if halted:
        log("停牌/退市检测: %s 已停止成交，已排除出所有名单"
            % ", ".join(sorted(t.replace(".AX", "") for t in halted)))

    step("正在计算板块资金流、热度与做空压力 ...", 26)
    panel = S.score_sectors(S.build_sector_panel(px, spct, ssha))
    stock_rows = PK.score_stocks(panel, halted=halted)

    # Where the short sellers got in, and whether they are under water.
    try:
        scost = SC.build(shorts, px, [t.replace(".AX", "") for t in tickers])
    except Exception:
        log("空头成本估算失败(不影响其余部分): %s" % traceback.format_exc().splitlines()[-1])
        scost = {}

    # Volume-by-price: where the crowd's cost actually sits. Measurement only --
    # it is attached to the cards and deliberately kept out of every score.
    try:
        profiles = SM.build_profiles(px, tickers)
    except Exception:
        log("成交量分布失败(不影响其余部分): %s" % traceback.format_exc().splitlines()[-1])
        profiles = {}

    rec = PK.build_recommendations(panel, stock_rows, n=3, profiles=profiles,
                                   short_cost=scost)

    step("正在定位资金流入/流出的具体股票 ...", 30)
    try:
        money_flow = SM.build_money_flow_panel(panel, px, profiles=profiles,
                                              halted=halted, short_cost=scost)
        if money_flow:
            money_flow["as_of"] = str(asof.date())
    except Exception:
        log("资金面板失败: %s" % traceback.format_exc().splitlines()[-1])
        money_flow = None

    step("正在采集机构持股/董事公告 ...", 33)
    ann = {"available": False, "scored": False, "days_collected": 0, "total": 0,
           "sectors": [], "recent": []}
    try:
        if force or not AN.polled_today():
            AN.poll(log=log)
            AN.parse_pending(limit=40, log=log)
        ann = AN.summary(ticker_to_sector(), {k: v["name"] for k, v in SECTORS.items()})
    except Exception:
        log("公告采集失败(不影响其余部分): %s" % traceback.format_exc().splitlines()[-1])
    arch = {}

    step("正在做选股评分的横截面回测验证 ...", 34)
    try:
        val = V.run_validation(px, spct, tickers, ticker_to_sector(), horizon=20, n_side=10)
    except Exception:
        log("验证失败: %s" % traceback.format_exc().splitlines()[-1])
        val = None

    step("正在构建市场因子 ...", 42)
    X = F.build_market_features(px, spct)
    Xf = X.dropna(thresh=int(X.shape[1] * 0.7))

    tgs = _targets(bd)
    mcache = D._load("models.pkl") or {}
    if mcache.get("__ver") != MODEL_VERSION:
        if mcache:
            log("模型代码版本已变（%s -> %s），弃用旧的走向前缓存，本次全量重训"
                % (mcache.get("__ver"), MODEL_VERSION))
        mcache = {}
    step("正在做走向前样本外建模 (%d 个目标) ..." % len(tgs), 48)
    results, new_cache = [], {}
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(_fit_target, tg, Xf, mcache, log): tg for tg in tgs}
        done = 0
        for fu in cf.as_completed(futs):
            done += 1
            key = futs[fu]["key"]
            try:
                out = fu.result()
                if out:
                    payload, cc = out
                    results.append(payload)
                    new_cache[key] = cc
            except Exception:
                log("目标建模失败 [%s]: %s" % (key, traceback.format_exc().splitlines()[-1]))
            step("建模进度 %d/%d" % (done, len(tgs)), 48 + int(32 * done / len(tgs)))
    new_cache["__ver"] = MODEL_VERSION
    D._save("models.pkl", new_cache)
    order = {t["key"]: i for i, t in enumerate(tgs)}
    results.sort(key=lambda r: order.get(r["key"], 99))

    step("正在生成信号明细与回测 ...", 86)
    ya1 = (bd.shift(-1) / bd - 1.0 > 0).astype(float)
    rows = M.signal_scoreboard(Xf, ya1.reindex(Xf.index), F.PRIOR_SIGN,
                               F.FEATURE_LABEL, F.FEATURE_BLOCK)
    blocks = M.block_scores(rows)

    curve = None
    dirs = [r for r in results if r["group"] == "direction"]
    if dirs:
        best = max(dirs, key=lambda r: (r["metrics"] or {}).get("auc") or 0)
        cal = (new_cache.get(best["key"]) or {}).get("cal")
        if cal is not None:
            curve = M.strategy_curve(cal, bd.pct_change().reindex(cal.index), 0.52)
            if curve:
                curve["target"] = best["short"]

    # ---- sector payload ----
    sec_out = []
    for k in sorted(panel, key=lambda k: -panel[k]["flow"]["score"]):
        p = panel[k]
        sp_hist = p["series"]["short_pct"].dropna().tail(120) if len(p["series"]["short_pct"]) else pd.Series(dtype=float)
        sec_out.append({
            "key": k, "name": p["name"], "en": p["en"], "n": p["n"], "cap_aud": p["cap_aud"],
            "flow": p["flow"], "heat": p["heat"], "short": p["short"],
            "extension": p["extension"], "stage": p["stage"], "rotation": p["rotation"],
            "perf": p["perf"], "breadth": p["breadth"], "raw": p["raw"],
            "signed_flow_20d_m": (p["raw"]["signed_flow_20d"] or 0) / 1e6,
            "stocks": sorted(p["stocks"], key=lambda s: -(s["ret_20d"] or 0)),
            "short_history": {"dates": [str(x.date()) for x in sp_hist.index],
                              "v": [round(float(v), 3) for v in sp_hist.values]},
        })

    top_short_stocks = sorted([r for r in stock_rows if r.get("short_pct") is not None],
                              key=lambda r: -(r.get("short_pct") or 0))[:15]
    top_build = sorted([r for r in stock_rows if r.get("short_chg_20d") is not None],
                       key=lambda r: -(r.get("short_chg_20d") or 0))[:15]

    hsi_hist = bd.tail(260)
    keep = ("ticker", "code", "sector_name", "px", "ret_20d", "ret_60d", "rsi14", "cmf20",
            "short_pct", "short_chg_20d", "short_pctile_1y", "days_to_cover",
            "dist_ma50", "adv_aud", "long_score", "short_score")

    report = {
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "as_of": str(asof.date()),
        "short_as_of": str(shorts["date"].max().date()) if len(shorts) else None,
        "benchmark": {
            "ticker": BENCHMARK, "name": "标普/ASX 200",
            "last": float(bd.iloc[-1]),
            "chg_1d": float(bd.iloc[-1] / bd.iloc[-2] - 1),
            "chg_5d": float(bd.iloc[-1] / bd.iloc[-6] - 1),
            "chg_20d": float(bd.iloc[-1] / bd.iloc[-21] - 1),
            "chg_ytd": float(bd.iloc[-1] / bd[bd.index.year == asof.year].iloc[0] - 1),
            "rv20": float(I.realized_vol(bd, 20).iloc[-1]),
            "history": {"dates": [str(x.date()) for x in hsi_hist.index],
                        "v": [round(float(v), 2) for v in hsi_hist.values]},
        },
        "direction": _direction_summary(results),
        "forecasts": results,
        "sectors": sec_out,
        "money_flow": money_flow,
        "short_cost": sorted(scost.values(), key=lambda r: r["pnl"])[:12],
        "data_health": {
            "halted": [{"code": t.replace(".AX", ""), **D.trading_status(px, [t])[t]}
                       for t in sorted(halted)],
            "universe": len(tickers),
        },
        "announcements": ann,
        "archive": arch,
        "recommendation": rec,
        "validation": val,
        "most_shorted": [{k: r.get(k) for k in keep} for r in top_short_stocks],
        "short_building": [{k: r.get(k) for k in keep} for r in top_build],
        "signals": rows,
        "blocks": blocks,
        "block_labels": F.BLOCKS,
        "macro": _macro_snapshot(px),
        "backtest": curve,
        "coverage": {
            "stocks": len(tickers), "sectors": len(sec_out),
            "trading_days": int(px["close"].shape[0]),
            "history_from": str(px["close"].index[0].date()),
            "features": int(Xf.shape[1]), "model_rows": int(Xf.shape[0]),
            "short_stocks": int(spct.iloc[-1].notna().sum()),
            "short_days": int(shorts["date"].nunique()) if len(shorts) else 0,
            "short_from": str(shorts["date"].min().date()) if len(shorts) else None,
            "model_version": MODEL_VERSION,
        },
        "runtime_sec": round(time.time() - t_all, 1),
    }
    # Point-in-time archive: the only part of the system that improves by being run.
    # Written after the report is assembled so a failure here cannot lose the report.
    try:
        AR.record(report, log=log)
        report["archive"] = AR.stats()
    except Exception:
        log("每日存档失败(不影响本次结果): %s" % traceback.format_exc().splitlines()[-1])
        report["archive"] = AR.stats()

    step("完成 (%.1fs)" % report["runtime_sec"], 100)
    return _clean(report)


def save(report, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    return path
