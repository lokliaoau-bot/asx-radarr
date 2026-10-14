# -*- coding: utf-8 -*-
"""2026-10-14 审查轮的两个「更多数据」实验。门槛都是跑之前写死的，见交接文档五点六。

    python tools/experiments/review_2026_10.py e1     # 扩池样本外检验（会联网下载新股票行情/行业）
    python tools/experiments/review_2026_10.py e2     # 分红因子（零额外下载）

E1：看空评分（picks.SHORT_WEIGHTS，公式不动）在**从未参与设计**的新股票上是否还成立。
    门槛（20 日，NW t）：<= -2.0 复现；(-2, 0) 方向一致但不能确认；>= 0 失败。
E2：从 adjclose/close 在除息日的跳变还原现金分红，检验
    A 股息率（假设高股息之后相对强）、B 股息变化（假设砍息之后相对弱）。
    门槛：现有池 20 日 NW t >= 2.0 且方向一致；不过就停，不去新池里再找。

结果写到同目录 e1_result.json / e2_result.json。
"""
from __future__ import annotations

import json
import os
import pickle
import sys
import time

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import asxquant  # noqa: E402,F401
from asxquant import datafeed as D, validate as V  # noqa: E402
from asxquant.config import (BORROW_BPS_PA, COST_BPS, all_stock_tickers,  # noqa: E402
                             ticker_to_sector)

HERE = os.path.dirname(os.path.abspath(__file__))
INFO = os.path.join(HERE, "e1_new_names_info.json")
NEW_PX = os.path.join(ROOT, "cache", "exp_e1_prices.pkl")
FIELDS = [("Close", "close"), ("Open", "open"), ("High", "high"), ("Low", "low"),
          ("Volume", "volume"), ("Adj Close", "adjclose")]
YMAP = {"Basic Materials": "materials", "Industrials": "industrials", "Technology": "infotech",
        "Financial Services": "financials", "Consumer Cyclical": "discretionary",
        "Real Estate": "realestate", "Healthcare": "healthcare",
        "Communication Services": "telecom", "Energy": "energy",
        "Consumer Defensive": "staples", "Utilities": "utilities"}


def new_names(shorts, refresh=False):
    """ASIC 覆盖 >=30% 交易日、不在现有池里的代码 -> Yahoo 6 年行情 -> 流动性 -> 去掉 ETF。"""
    import yfinance as yf
    if not refresh and os.path.exists(NEW_PX) and os.path.exists(INFO):
        with open(NEW_PX, "rb") as f:
            px = pickle.load(f)
        with open(INFO, encoding="utf-8") as f:
            info = json.load(f)
    else:
        cur = {t.replace(".AX", "") for t in all_stock_tickers()}
        g = shorts.dropna(subset=["short_pct"]).groupby("code")
        cov = g.size() / shorts["date"].nunique()
        codes = sorted(c for c in cov[cov >= 0.3].index if c not in cur)
        raw = yf.download([c + ".AX" for c in codes], period="6y", interval="1d", progress=False,
                          auto_adjust=False, threads=True, group_by="column")
        px = {}
        for field, key in FIELDS:
            df = raw[field].copy()
            df.index = pd.to_datetime(df.index).tz_localize(None)
            px[key] = df.sort_index()
        good = [c for c in px["close"].columns if px["close"][c].notna().sum() >= 300]
        px = {k: v[good] for k, v in px.items()}
        with open(NEW_PX, "wb") as f:
            pickle.dump(px, f)
        dv = (px["close"] * px["volume"]).iloc[-500:].median()
        info = {}
        for t in sorted(dv[dv > 1.5e6].index):
            try:
                i = yf.Ticker(t).info
                info[t] = {"type": i.get("quoteType"), "sector": i.get("sector"),
                           "industry": i.get("industry"), "name": i.get("shortName")}
            except Exception as e:  # 取不到就不纳入，不猜行业
                info[t] = {"error": str(e)[:80]}
            time.sleep(0.2)
        with open(INFO, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=0)
    names = sorted(t for t, v in info.items() if v.get("type") == "EQUITY" and v.get("sector") in YMAP)
    return px, names, {t: YMAP[info[t]["sector"]] for t in names}


def merged(px0, px1):
    idx = px0["close"].index
    return {k: pd.concat([px0[k], px1[k].reindex(idx)[[c for c in px1[k].columns
                                                        if c not in px0[k].columns]]], axis=1)
            for _, k in FIELDS}


def e1():
    px0, sh = D.fetch_prices(log=lambda m: None), D.fetch_shorts(log=lambda m: None)
    px1, new, smap = new_names(sh)
    px = merged(px0, px1)
    idx = px0["close"].index
    cur = [t for t in all_stock_tickers() if t in px0["close"].columns]

    def run(tk, sectors, h=20):
        spct, ssha = D.shorts_panel(sh, tk, idx)
        v = V.run_validation(px, spct, tk, sectors, horizon=h, n_side=10, short_shares=ssha,
                             cost_bps=COST_BPS, borrow_bps_pa=BORROW_BPS_PA, n_trials=60)
        if v.get("error"):
            return v
        q = v.get("quintile_short") or {}
        return {"names": len(tk), "start": v["start"], "end": v["end"],
                "ic_short_mean": v["ic_short"]["mean_ic"], "ic_short_t": v["ic_short"]["t_stat"],
                "ic_long_t": v["ic_long"]["t_stat"], "q5q1_spread": q.get("spread"),
                "q5q1_t": q.get("t_stat"),
                "short_basket_cagr": (v["legs"]["short_basket"] or {}).get("cagr"),
                "market_cagr": (v["legs"]["market"] or {}).get("cagr"),
                "ic_short_by_year": {y["year"]: y["mean_ic"] for y in v["ic_short"].get("by_year", [])}}

    both = dict(ticker_to_sector())
    both.update(smap)
    res = {"current": run(cur, ticker_to_sector()), "new_only": run(new, smap),
           "combined": run(cur + new, both),
           "new_only_h5": run(new, smap, 5), "new_only_h10": run(new, smap, 10)}
    t = res["new_only"].get("ic_short_t")
    res["verdict"] = ("replicates" if t is not None and t <= -2.0 else
                      "direction_consistent_not_confirmed" if t is not None and t < 0 else "fails")
    return res


def dividends(close, adj):
    """除息日 adj/close 比值变大：D = 前收 x (1 - r_{t-1}/r_t)。滤掉 <0.05% 的浮点噪声。"""
    r = adj / close
    ratio = r.shift(1) / r
    return (close.shift(1) * (1 - ratio)).where((ratio < 0.9995) & (ratio > 0.5)).fillna(0.0)


def e2():
    px = D.fetch_prices(log=lambda m: None)
    tk = [t for t in all_stock_tickers() if t in px["close"].columns]
    C, A, Vo = px["close"][tk], px["adjclose"][tk], px["volume"][tk]
    dv = dividends(C, A)
    ttm = dv.rolling(252, min_periods=252).sum()
    prev = ttm.shift(252)
    factors = {"yield": (ttm / C).where(C.rolling(252).count() == 252),
               "div_change": (ttm / prev - 1).where(prev > 0)}
    adv = (C * Vo).rolling(20, min_periods=5).mean()
    traded = ~(Vo == 0)
    m = (adv > 3e6) & traded & A.notna()
    res = {"dividend_events": int((dv > 0).sum().sum()),
           "spot_check_cba": {str(i.date()): round(float(v), 3)
                              for i, v in dv["CBA.AX"][dv["CBA.AX"] > 0].loc["2024-06-01":].items()}}
    for h in (20, 10, 5):
        fwd = (A.shift(-(h + 1)) / A.shift(-1) - 1).where(traded.shift(-1).fillna(False).astype(bool))
        for name, f in factors.items():
            ic = V._ic(f.where(m).loc["2016-01-01":], fwd.loc["2016-01-01":])
            st = V._overlap_stats(ic, h) or {}
            res["%s_h%d" % (name, h)] = {"mean_ic": round(float(ic.mean()), 4), "nw_t": st.get("t_stat"),
                                        "days": int(len(ic))}
    passed = [n for n in factors if (res[n + "_h20"]["nw_t"] or 0) >= 2.0]
    res["verdict"] = {n: ("passes" if n in passed else "fails") for n in factors}
    return res


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    which = sys.argv[1] if len(sys.argv) > 1 else "e1"
    out = {"e1": e1, "e2": e2}[which]()
    out["run_at"] = time.strftime("%Y-%m-%d %H:%M")
    with open(os.path.join(HERE, which + "_result.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1)[:3000])
