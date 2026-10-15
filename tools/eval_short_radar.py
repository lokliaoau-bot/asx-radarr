# -*- coding: utf-8 -*-
"""实测 AI Short Radar（ChatGPT 版）technical-v1 看空分有没有预测力。

    python tools/eval_short_radar.py            # 写 web/short/evidence.json

ChatGPT 那边做了 12 个版本的审计，却从没拿真实行情检验过这个分数。这里补上：

* 公式：直接复刻 short_radar/analysis.py 的趋势分 + 成交量分（权重 5/10/20 日各一套），
  并抽 400 个点与 short_radar.engine 自己的函数逐点比对，误差必须 < 1e-9，
  否则直接报错 —— 验的必须是生产公式，不是一个"差不多"的版本。
* 收益：按它自己的 EVAL-SPEC，t+1 开盘进场、持有第 h 个交易日收盘出场，复权价。
* 统计：每日横截面 Spearman IC，Newey-West(lag=h-1) t 值（重叠收益不修正 t 会大 3-5 倍）；
  不重叠调仓的五分位超额收益；对照一条最朴素的规则「过去 20 天跌得多」。
* 保留期：评估窗口固定截止 EVAL_END。之后的数据一律不看，留给以后真正的样本外检验。
  不要把 EVAL_END 往后挪来"更新结果"——挪一次，保留期就被偷看一次。

已知偏差（写进 evidence.json，页面上也显示）：股票池是今天还在上市的公司，
退市的不在里面（幸存者偏差），通常会让"跌多了反弹"看起来更强。市场对照用
^AXJO 价格指数代替 STW（缓存里没有 STW）；IC 是横截面排序，不受对照选择影响。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pickle
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from short_radar import engine  # noqa: E402
from short_radar.engine import WEIGHTS  # noqa: E402
from asxquant import validate as VAL  # noqa: E402

EVAL_END = "2026-02-27"
OUT = os.path.join(ROOT, "web", "short", "evidence.json")
BENCH = "^AXJO"


def load():
    with open(os.path.join(ROOT, "cache", "prices.pkl"), "rb") as f:
        o = pickle.load(f)["obj"]
    grid = o["close"][BENCH].dropna().index
    grid = grid[grid <= pd.Timestamp(EVAL_END) + pd.Timedelta(days=40)]  # 留够前瞻收益的尾巴
    stocks = [c for c in o["close"].columns if c.endswith(".AX")]
    g = lambda k: o[k].reindex(grid)[stocks]   # noqa: E731
    return grid, stocks, g("close"), g("open"), g("volume"), g("adjclose"), o["close"][BENCH].reindex(grid)


def factors(C, V, B):
    clip = lambda x: x.clip(0.0, 1.0)   # noqa: E731
    ma20, ma60 = C.rolling(20).mean(), C.rolling(60).mean()
    chg5 = ma20 / C.shift(5).rolling(20).mean() - 1
    rs = (C / C.shift(20) - 1).sub(B / B.shift(20) - 1, axis=0)
    trend = (clip((ma20 - C) / (0.08 * ma20)) + clip((ma60 - C) / (0.12 * ma60))
             + clip(-rs / 0.15) + clip(-chg5 / 0.05)) / 4
    dc = C.diff()
    down20 = V.where(dc < 0, 0.0).shift(1).rolling(20).sum()
    up20 = V.where(dc > 0, 0.0).shift(1).rolling(20).sum()
    avg20 = V.shift(1).rolling(20).mean()
    dist = (0.6 * clip((down20 / np.maximum(up20, 1) - 1) / 2)
            + 0.4 * clip((V / avg20 - 1) / 1.5) * (C < C.shift(1)).astype(float)).where(avg20 > 0)
    ok = (C.rolling(252).count() == 252) & (V.rolling(252).count() == 252) & (C > 0)
    return trend.where(ok), dist.where(ok), ok


def crosscheck(C, V, B, trend, dist, n=400):
    """逐点对照 short_radar.engine —— 复刻错了就不许出结果。"""
    rng = np.random.default_rng(0)
    done = 0
    for _ in range(n):
        s = C.columns[rng.integers(C.shape[1])]
        i = int(rng.integers(300, len(C) - 1))
        if not (np.isfinite(trend[s].iat[i]) and np.isfinite(dist[s].iat[i])):
            continue
        cs, vol, bm = (x.iloc[i - 251:i + 1].tolist() for x in (C[s], V[s], B))
        m20, m60 = sum(cs[-20:]) / 20, sum(cs[-60:]) / 60
        t_ref = engine.trend(cs[-1], m20, m60, cs[-1] / cs[-21] - 1 - (bm[-1] / bm[-21] - 1),
                             m20 / (sum(cs[-25:-5]) / 20) - 1)
        up = down = 0
        for k in range(len(cs) - 21, len(cs) - 1):
            if cs[k] < cs[k - 1]:
                down += vol[k]
            elif cs[k] > cs[k - 1]:
                up += vol[k]
        d_ref = engine.distribution(down, up, vol[-1], sum(vol[-21:-1]) / 20, cs[-1] < cs[-2])
        if abs(t_ref - trend[s].iat[i]) > 1e-9 or abs(d_ref - dist[s].iat[i]) > 1e-9:
            raise AssertionError("复刻公式与 short_radar.engine 不一致: %s @ %s" % (s, C.index[i]))
        done += 1
    return done


def nw_t(x, lag):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    n, e = len(x), x - x.mean()
    lrv = (e @ e) / n
    for k in range(1, lag + 1):
        lrv += 2 * (1 - k / (lag + 1)) * (e[k:] @ e[:-k]) / n
    return float(x.mean() / np.sqrt(lrv / n)), n


def daily_ic(X, Y, min_n=30):
    """返回值保留日历槽（算不出的日子 NaN）：不重叠检验按日历每 h 天取一点。"""
    out = {}
    for d in X.index:
        ok = X.loc[d].notna() & Y.loc[d].notna()
        if ok.sum() >= min_n:   # 先掩码再求秩（asxquant/validate.py v2 修过的坑）
            out[d] = X.loc[d][ok].rank().corr(Y.loc[d][ok].rank())
    return pd.Series(out, dtype=float).reindex(X.index)


def quintiles(S, F, h):
    """不重叠调仓日的五分组超额收益。

    2026-10-15 修（ChatGPT 审查 H-EVAL-01，复核属实）：以前先按「未来收益有没有」
    筛掉股票、再分五组——未来缺价（停牌、退市）会反过来改变当时的分组。现在**只按
    当天的评分**分组，结果再附上去；组里没有结果的成员单独计数，不替补、不当 0。
    """
    qs, unresolved, members = [], 0, 0
    for d in S.index[::h]:
        x = S.loc[d].dropna()
        if len(x) < 50:
            continue
        q = pd.qcut(x.rank(method="first"), 5, labels=False)
        y = F.loc[d].reindex(x.index)
        y = y - y.mean()                      # 相对同一批成员里有结果者的平均
        members += len(x)
        unresolved += int(y.isna().sum())
        qs.append([y[q == k].mean() for k in range(5)])
    return np.array(qs), members, unresolved


def main():
    grid, stocks, C, O, V, A, B = load()
    trend, dist, ok = factors(C, V, B)
    checked = crosscheck(C, V, B, trend, dist)
    entry = (O * (A / C)).shift(-1)
    sl = slice(grid[260], pd.Timestamp(EVAL_END))
    mom = (-(C / C.shift(20) - 1)).where(ok)
    res = {}
    for h in (5, 10, 20):
        w0, w1 = WEIGHTS[h][:2]
        score = w0 * trend + w1 * dist
        fwd = A.shift(-h) / entry - 1
        S, F = score.loc[sl], fwd.loc[sl]
        ic = daily_ic(S, F)
        st = VAL._overlap_stats(ic, h)            # 判决用不重叠 t（与主站同一口径）
        t, n = nw_t(ic.dropna().values, h - 1)  # 旧算法，只作诊断
        bic = daily_ic(mom.loc[sl], F)
        bst = VAL._overlap_stats(bic, h)
        qs, members, unresolved = quintiles(S, F, h)
        spread = qs[:, 4] - qs[:, 0]
        res[str(h)] = {
            "mean_ic": round(float(ic.mean()), 4), "t": st["t_stat"], "p": st["p_value"],
            "n_nonoverlap": st["n_nonoverlap"], "phases_same_sign": st["phases_same_sign"],
            "phases": st["phases"], "nw_t": round(t, 2), "days": n,
            "baseline_mom20_ic": round(float(bic.mean()), 4), "baseline_mom20_t": bst["t_stat"],
            "baseline_mom20_nw_t": round(nw_t(bic.dropna().values, h - 1)[0], 2),
            "rebalances": len(qs), "quintile_members": members, "quintile_unresolved": unresolved,
            "quintile_excess_pct": [round(float(v) * 100, 3) for v in qs.mean(0)],
            "top_minus_bottom_pct": round(float(spread.mean()) * 100, 3),
            "top_minus_bottom_t": round(float(spread.mean() / (spread.std(ddof=1) / np.sqrt(len(spread)))), 2),
            "top_quintile_underperform_rate": round(float((qs[:, 4] < 0).mean()), 3),
            "ic_by_year": {str(k): round(float(v), 4) for k, v in ic.dropna().groupby(ic.dropna().index.year).mean().items()},
        }
    worst = max(abs(r["t"] or 0) for r in res.values())
    out = {
        "generated": dt.date.today().isoformat(),
        "formula": "technical-v1（趋势分 + 成交量分；公司财务/估值/消息三项未接入）",
        "window": [str(grid[260].date()), EVAL_END],
        "holdout": "%s 之后的数据未看，留作以后的样本外检验" % EVAL_END,
        "universe": len(stocks), "pointwise_crosscheck": checked,
        "entry_exit": "t+1 开盘进场，第 h 个交易日收盘出场，复权价",
        "inference": ("判决用固定第 0 个起点、每 h 个交易日取一天的不重叠 t（t 分布）；"
                      "nw_t 是旧的 Newey-West(lag=h-1)，模拟证明它在慢变评分上误报翻倍，只作诊断"),
        "caveats": ["股票池是今天仍上市的公司（幸存者偏差，通常让「跌多了反弹」显得更强）",
                    "市场对照用 ^AXJO 价格指数代替 STW；IC 是横截面排序，不受影响"],
        "verdict": "none" if worst < 2 else "check",
        "horizons": res,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps({h: {k: r[k] for k in ("mean_ic", "t", "p", "nw_t", "top_minus_bottom_pct",
                                              "quintile_unresolved", "baseline_mom20_t")} for h, r in res.items()},
                     ensure_ascii=False))
    print("crosscheck", checked, "->", OUT)


if __name__ == "__main__":
    main()
