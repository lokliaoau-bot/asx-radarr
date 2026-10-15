# -*- coding: utf-8 -*-
"""独立复核：几种显著性检验在「评分与收益毫无关系」时会误报多少。

    python tools/experiments/fpr_check.py [reps=400]    # 结果写 fpr_check_result.json

背景：ChatGPT 2026-10-15 的合成自检说「每日 Newey-West(lag=h-1) 误报偏高」。这里用一套
独立写的模拟复核，并且加上它没测的、更像真实情况的两种零假设：
  iid        —— 评分每天随机重抽；
  persistent —— 评分像空头持仓那样变化很慢（AR(1)，phi=0.98）；
  factor     —— 变化很慢 + 11 个板块联动（收益有板块因子，评分带板块成分）。
每格 reps 次，150 只股票 × 1150 天（≈ 2022 年以来的 ASIC 样本长度），名义水平 5%。

2026-10-15 实测（400 次/格）：NW lag=h-1 在 persistent/factor 下误报 10-13%；
lag 加到 3h 仍 7-11%；固定第 0 起点的不重叠 t 是 4-7%（尺寸正确）。
→ validate._overlap_stats 从这天起用不重叠 t 判决，NW 只作诊断。
"""
import json
import os
import sys
import time

import numpy as np


def ranks(a):
    return a.argsort(axis=1).argsort(axis=1).astype(float)


def daily_ic(score, fwd):
    s, f = ranks(score), ranks(fwd)
    s -= s.mean(1, keepdims=True)
    f -= f.mean(1, keepdims=True)
    return (s * f).sum(1) / np.sqrt((s * s).sum(1) * (f * f).sum(1))


def nw_t(x, lag):
    n, e = len(x), x - x.mean()
    lrv = e @ e / n
    for k in range(1, lag + 1):
        lrv += 2 * (1 - k / (lag + 1)) * (e[k:] @ e[:-k]) / n
    return x.mean() / np.sqrt(lrv / n)


def phase_t(x, h):
    w = x[0::h]
    return w.mean() / (w.std(ddof=1) / np.sqrt(len(w)))


def simulate(rng, n_stocks, n_days, h, kind, phi=0.98, groups=11):
    grp = rng.integers(0, groups, n_stocks)
    if kind == "factor":
        r = rng.normal(0, 0.018, (n_days + h + 1, n_stocks)) + \
            rng.normal(0, 0.008, (n_days + h + 1, groups))[:, grp]
    else:
        r = rng.normal(0, 0.02, (n_days + h + 1, n_stocks))
    cr = np.vstack([np.zeros((1, n_stocks)), np.cumsum(r, 0)])
    t = np.arange(n_days)
    fwd = cr[t + h + 1] - cr[t + 1]                      # t+1 .. t+h 的收益
    if kind == "iid":
        return daily_ic(rng.normal(size=(n_days, n_stocks)), fwd)
    z, sec = rng.normal(size=n_stocks), rng.normal(size=groups)
    out = np.empty((n_days, n_stocks))
    for i in range(n_days):
        z = phi * z + np.sqrt(1 - phi ** 2) * rng.normal(size=n_stocks)
        sec = phi * sec + np.sqrt(1 - phi ** 2) * rng.normal(size=groups)
        out[i] = z + (sec[grp] if kind == "factor" else 0.0)
    return daily_ic(out, fwd)


def main():
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    rng = np.random.default_rng(20261015)
    res, t0 = {}, time.time()
    for kind in ("iid", "persistent", "factor"):
        for h in (5, 10, 20):
            rej = {"nw_lag_h-1": 0, "nw_lag_2h": 0, "nw_lag_3h": 0, "phase0_nonoverlap": 0}
            for _ in range(reps):
                ic = simulate(rng, 150, 1150, h, kind)
                rej["nw_lag_h-1"] += abs(nw_t(ic, h - 1)) > 1.96
                rej["nw_lag_2h"] += abs(nw_t(ic, 2 * h)) > 1.96
                rej["nw_lag_3h"] += abs(nw_t(ic, 3 * h)) > 1.96
                rej["phase0_nonoverlap"] += abs(phase_t(ic, h)) > 1.96
            res["%s_h%d" % (kind, h)] = {k: round(int(v) / reps, 3) for k, v in rej.items()}
            print(kind, h, res["%s_h%d" % (kind, h)], "%ds" % (time.time() - t0), flush=True)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fpr_check_result.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"reps": reps, "stocks": 150, "days": 1150, "nominal": 0.05,
                   "seed": 20261015, "false_positive_rate": res}, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
