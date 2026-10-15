# -*- coding: utf-8 -*-
"""检验力：如果看空分**真的有效**，按现在的样本量，严格检验有多大机会把它测出来？

    python tools/experiments/power_check.py

用真实数据里「第 0 起点不重叠样本」每个点的实际离散度（sd），按非中心 t 分布算：
真实 IC 为 mu 时，n 个不重叠样本下 p<0.05（双侧）的概率；以及达到 80% 检验力需要几段、
按 20 日一段要多少年。回答：「不显著」有多少是因为信号弱，有多少是因为样本太少。
"""
import json
import os
import sys

import numpy as np
from scipy import stats

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


def power(mu, sd, n, alpha=0.05):
    df = n - 1
    crit = stats.t.ppf(1 - alpha / 2, df)
    ncp = abs(mu) / (sd / np.sqrt(n))
    return float(stats.nct.sf(crit, df, ncp) + stats.nct.cdf(-crit, df, ncp))


def n_for(mu, sd, target=0.8):
    n = 5
    while power(mu, sd, n) < target and n < 5000:
        n += 1
    return n


def real_sd(h=20):
    import asxquant  # noqa: F401
    from asxquant import datafeed as D, validate as V
    from asxquant.config import all_stock_tickers, ticker_to_sector
    px = D.fetch_prices(log=lambda m: None)
    sh = D.fetch_shorts(log=lambda m: None)
    tk = [t for t in all_stock_tickers() if t in px["close"].columns]
    sp, ss = D.shorts_panel(sh, tk, px["close"].index)
    F = V.build_factor_panel(px, sp, tk, ticker_to_sector(), short_shares=ss)
    _, short_s = V.composite_scores(F)
    a = F["adj"]
    vol = px["volume"][tk].reindex(index=a.index)
    traded = ~(vol == 0)
    fwd = (a.shift(-(h + 1)) / a.shift(-1) - 1).where(traded.shift(-1).fillna(False).astype(bool))
    mask = (F["adv"] > 3e6) & F["short_pct"].notna() & a.notna() & traded
    start = mask.sum(axis=1).ge(30).idxmax()
    ic = V._ic(short_s.where(mask).loc[start:], fwd.loc[start:])
    w = ic.iloc[0::h].dropna()
    return float(w.mean()), float(w.std(ddof=1)), len(w)


def main():
    mean, sd, n = real_sd()
    out = {"observed_phase0_mean_ic": round(mean, 4), "observed_phase0_sd": round(sd, 4), "n_now": n,
           "power_now": {}, "segments_for_80pct": {}, "years_for_80pct_at_20d": {}}
    for mu in (-0.02, -0.03, -0.04, -0.05):
        k = "%.2f" % mu
        out["power_now"][k] = round(power(mu, sd, n), 3)
        need = n_for(mu, sd)
        out["segments_for_80pct"][k] = need
        out["years_for_80pct_at_20d"][k] = round(need * 20 / 252, 1)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "power_check_result.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
