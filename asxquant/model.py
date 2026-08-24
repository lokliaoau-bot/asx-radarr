# -*- coding: utf-8 -*-
"""Walk-forward probability engine.

Design decisions and the literature they come from
--------------------------------------------------
* Every number reported is **out-of-sample**. Models are refitted on an expanding
  window and only ever see data strictly older than the forecast date
  (Welch & Goyal 2008 -- in-sample fit is not evidence).
* Training labels that overlap the forecast date are **purged**, plus an embargo
  (Lopez de Prado 2018), because an h-day forward return leaks h days of future
  information into the label.
* Three forecasters are combined: penalised logistic, gradient-boosted trees
  (Gu, Kelly & Xiu 2020) and a mean-of-univariate-forecasts combination
  (Rapach, Strauss & Zhou 2010, whose central finding is that the naive average
  of simple forecasts beats any single sophisticated one out of sample).
* The combined probability is then **shrunk toward the unconditional base rate**
  in proportion to measured out-of-sample discrimination (the spirit of
  Campbell & Thompson 2008). No demonstrated skill => the report falls back to
  the base rate rather than inventing conviction.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from .config import EMBARGO_EXTRA, MIN_TRAIN

REFIT_EVERY = 21          # trading days between refits (monthly, standard practice)
LOGIT_C = 0.08            # strong L2 -- the signal-to-noise ratio here is low


# ----------------------------------------------------------------------
def _prep(Xtr, Xte):
    med = np.nanmedian(Xtr, axis=0)
    med = np.where(np.isfinite(med), med, 0.0)
    Xtr = np.where(np.isfinite(Xtr), Xtr, med)
    Xte = np.where(np.isfinite(Xte), Xte, med)
    mu = Xtr.mean(axis=0)
    sd = Xtr.std(axis=0)
    sd = np.where(sd > 1e-9, sd, 1.0)
    return (Xtr - mu) / sd, (Xte - mu) / sd


def _fit_predict(Xtr, ytr, Xte):
    """Return dict of model-name -> predicted P(up) for the rows of Xte."""
    out = {}
    Xtr_s, Xte_s = _prep(Xtr, Xte)

    if len(np.unique(ytr)) < 2:
        n = Xte.shape[0]
        base = float(ytr.mean()) if len(ytr) else 0.5
        return {"logit": np.full(n, base), "gbm": np.full(n, base), "combo": np.full(n, base)}

    try:
        lr = LogisticRegression(C=LOGIT_C, penalty="l2", solver="lbfgs", max_iter=2000)
        lr.fit(Xtr_s, ytr)
        out["logit"] = lr.predict_proba(Xte_s)[:, 1]
    except Exception:
        out["logit"] = np.full(Xte.shape[0], float(ytr.mean()))

    try:
        gb = HistGradientBoostingClassifier(
            max_depth=3, max_iter=120, learning_rate=0.04,
            min_samples_leaf=40, l2_regularization=1.0,
            early_stopping=False, random_state=0)
        gb.fit(Xtr_s, ytr)
        out["gbm"] = gb.predict_proba(Xte_s)[:, 1]
    except Exception:
        out["gbm"] = np.full(Xte.shape[0], float(ytr.mean()))

    # Rapach-Strauss-Zhou combination: mean of univariate forecasts
    ps = []
    for j in range(Xtr_s.shape[1]):
        try:
            m = LogisticRegression(C=1.0, solver="lbfgs", max_iter=500)
            m.fit(Xtr_s[:, [j]], ytr)
            ps.append(m.predict_proba(Xte_s[:, [j]])[:, 1])
        except Exception:
            pass
    out["combo"] = np.mean(ps, axis=0) if ps else np.full(Xte.shape[0], float(ytr.mean()))
    return out


def walk_forward(X: pd.DataFrame, y: pd.Series, horizon: int,
                 min_train=MIN_TRAIN, refit_every=REFIT_EVERY, log=None, cached=None):
    """Expanding-window, purged, embargoed out-of-sample probabilities.

    `cached` is a previously returned frame. Because the window only ever expands
    over data that has already settled, a block already predicted in an earlier run
    is bit-for-bit reproducible, so only blocks that are still empty are refitted.
    That turns a repeat refresh from minutes into seconds.
    """
    idx = X.index
    n = len(idx)
    Xv, yv = X.values.astype(float), y.values.astype(float)
    embargo = horizon + EMBARGO_EXTRA
    cols = ["logit", "gbm", "combo"]

    preds = {c: np.full(n, np.nan) for c in cols}
    if cached is not None and len(cached):
        prev = cached.reindex(idx)
        for c in cols:
            if c in prev.columns:
                preds[c] = prev[c].values.astype(float)

    n_fit = 0
    for s in range(min_train, n, refit_every):
        e = min(s + refit_every, n)
        if all(np.isfinite(preds[c][s:e]).all() for c in cols):
            continue                                  # already computed in a prior run
        train_end = s - embargo                       # purge overlapping labels
        if train_end < 100:
            continue
        m = np.isfinite(yv[:train_end])
        if m.sum() < 100:
            continue
        p = _fit_predict(Xv[:train_end][m], yv[:train_end][m], Xv[s:e])
        for c in cols:
            preds[c][s:e] = p[c]
        n_fit += 1

    df = pd.DataFrame(preds, index=idx)
    df["ensemble"] = df[cols].mean(axis=1)
    df.attrs["n_fit"] = n_fit
    return df


# ----------------------------------------------------------------------
def calibrate_expanding(p: pd.Series, y: pd.Series, horizon: int,
                        burn=252, refit=21, cached=None):
    """Leak-free probability calibration (Platt 1999 scaling on the log-odds).

    Raw model scores routinely discriminate (AUC > 0.5) while being badly calibrated
    (Brier skill < 0) because they are too confident. At each block the mapping is
    fitted only on outcomes that were already fully observed at that point --
    the same purge/embargo rule as the forecasts themselves -- so the calibrated
    series can be scored honestly.
    """
    idx = p.index
    n = len(idx)
    pv, yv = p.values.astype(float), y.reindex(idx).values.astype(float)
    out = np.full(n, np.nan)
    if cached is not None and len(cached):
        out = cached.reindex(idx).values.astype(float)
    emb = horizon + EMBARGO_EXTRA

    def _logit(x):
        x = np.clip(x, 1e-5, 1 - 1e-5)
        return np.log(x / (1 - x))

    first = None
    for i in range(n):
        if np.isfinite(pv[i]):
            first = i
            break
    if first is None:
        return pd.Series(out, index=idx)

    start = first + burn
    for s in range(start, n, refit):
        e = min(s + refit, n)
        if np.isfinite(out[s:e]).all():
            continue                                  # reuse a prior run's block
        te = s - emb
        m = np.isfinite(pv[:te]) & np.isfinite(yv[:te])
        if m.sum() < 150 or len(np.unique(yv[:te][m])) < 2:
            continue
        try:
            cal = LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000)
            cal.fit(_logit(pv[:te][m]).reshape(-1, 1), yv[:te][m])
            blk = pv[s:e]
            ok = np.isfinite(blk)
            if ok.any():
                res = np.full(blk.shape, np.nan)
                res[ok] = cal.predict_proba(_logit(blk[ok]).reshape(-1, 1))[:, 1]
                out[s:e] = res
        except Exception:
            continue
    return pd.Series(out, index=idx)



def naive_auc(naive, y, on_index):
    """Discrimination of a no-model benchmark, scored on the SAME rows as the model.

    Welch & Goyal (2008) -- already in this project's reference list -- showed that
    elaborate return predictors routinely fail to beat a trivial benchmark out of
    sample, and that comparing only against the base rate hides it. This project was
    doing exactly that: every forecast was judged against a coin flip, never against
    the obvious one-line alternative.

    It matters. Measured on this data, the 43-factor ensemble for "volatility rises"
    scores AUC 0.713 while `-realised_vol` alone -- literally "is volatility low right
    now" -- scores 0.775, and the naive version wins in every sub-period. A model that
    loses to one line of arithmetic should be labelled as such, not as "strongest in
    the system".
    """
    if naive is None:
        return None
    d = pd.concat([pd.Series(naive).rename("s"), pd.Series(y).rename("y")], axis=1)
    d = d.reindex(on_index).dropna()
    if len(d) < 60 or d["y"].nunique() < 2:
        return None
    try:
        return round(float(roc_auc_score(d["y"].values.astype(int), d["s"].values)), 4)
    except Exception:
        return None

def evaluate(p: pd.Series, y: pd.Series):
    """Out-of-sample scorecard for a probability forecast of a binary outcome."""
    d = pd.concat([p.rename("p"), y.rename("y")], axis=1).dropna()
    if len(d) < 60 or d["y"].nunique() < 2:
        return None
    pv, yv = d["p"].values, d["y"].values.astype(int)
    base = float(yv.mean())
    bs = brier_score_loss(yv, pv)
    bs_clim = brier_score_loss(yv, np.full_like(pv, base))
    try:
        auc = roc_auc_score(yv, pv)
    except Exception:
        auc = np.nan
    return {
        "n": int(len(d)),
        "base_rate": round(base, 4),
        "auc": round(float(auc), 4) if np.isfinite(auc) else None,
        "brier": round(float(bs), 5),
        "brier_climatology": round(float(bs_clim), 5),
        "brier_skill_score": round(float(1 - bs / bs_clim), 4) if bs_clim > 0 else None,
        "log_loss": round(float(log_loss(yv, np.clip(pv, 1e-6, 1 - 1e-6))), 5),
        "accuracy": round(float(((pv > 0.5).astype(int) == yv).mean()), 4),
        "hit_rate_high_conf": _high_conf_hit(pv, yv),
    }


def _high_conf_hit(pv, yv, band=0.05):
    m = np.abs(pv - 0.5) > band
    if m.sum() < 20:
        return None
    return {"n": int(m.sum()),
            "accuracy": round(float(((pv[m] > 0.5).astype(int) == yv[m]).mean()), 4)}


def shrink_to_base(p_last, base_rate, auc, full_skill_auc=0.60, naive_auc=None):
    """Shrink the raw probability toward the base rate by measured discrimination.

    `naive_auc` collapses the weight entirely when the model cannot beat the no-model
    benchmark. Discriminating better than a coin flip is not the bar: if "volatility is
    low right now" separates the outcome better than the ensemble does, then quoting a
    model probability actively misinforms, however far its AUC sits above 0.5. Falling
    back to the base rate at least says nothing rather than something worse than free.
    """
    if auc is None or not np.isfinite(auc):
        return base_rate, 0.0
    if naive_auc is not None and np.isfinite(naive_auc) and auc <= naive_auc:
        return base_rate, 0.0
    lam = float(np.clip((auc - 0.5) / (full_skill_auc - 0.5), 0.0, 1.0))
    return base_rate + lam * (p_last - base_rate), lam


def conditional_outcomes(p: pd.Series, fwd_ret: pd.Series, p_now: float, width=0.06):
    """What actually happened, out of sample, on the days the model looked like today."""
    d = pd.concat([p.rename("p"), fwd_ret.rename("r")], axis=1).dropna()
    if len(d) < 80:
        return None
    band = d[(d["p"] >= p_now - width) & (d["p"] <= p_now + width)]
    if len(band) < 30:
        # widen until we have a usable sample
        for w in (0.09, 0.12, 0.18, 0.30):
            band = d[(d["p"] >= p_now - w) & (d["p"] <= p_now + w)]
            if len(band) >= 30:
                width = w
                break
    if len(band) < 20:
        return None
    r = band["r"].values
    return {
        "n": int(len(r)),
        "band": round(float(width), 3),
        "hit_rate": round(float((r > 0).mean()), 4),
        "mean": round(float(np.mean(r)), 5),
        "median": round(float(np.median(r)), 5),
        "p10": round(float(np.percentile(r, 10)), 5),
        "p25": round(float(np.percentile(r, 25)), 5),
        "p75": round(float(np.percentile(r, 75)), 5),
        "p90": round(float(np.percentile(r, 90)), 5),
        "worst": round(float(np.min(r)), 5),
        "best": round(float(np.max(r)), 5),
    }


def strategy_curve(p: pd.Series, ret1d: pd.Series, thresh=0.52, cost_bps=5.0):
    """Long-when-confident vs buy-and-hold, using only OOS probabilities.

    The probability at date t is acted on at t+1, so no same-bar information is used.
    `cost_bps` is charged one-way on every change in position, because a daily signal
    at ~50% exposure turns over often enough that a gross curve would flatter it.
    """
    d = pd.concat([p.rename("p"), ret1d.rename("r")], axis=1).dropna()
    if len(d) < 120:
        return None
    pos = (d["p"].shift(1) > thresh).astype(float)
    turnover = pos.diff().abs().fillna(pos.abs())
    cost = turnover * (cost_bps / 1e4)
    gross = pos * d["r"]
    strat = gross - cost

    eq_s = (1 + strat).cumprod()
    eq_g = (1 + gross).cumprod()
    eq_b = (1 + d["r"]).cumprod()

    def _stats(x, e):
        yrs = max(len(x) / 252.0, 1e-9)
        cagr = e.iloc[-1] ** (1 / yrs) - 1
        vol = x.std(ddof=0) * np.sqrt(252)
        dd = float((e / e.cummax() - 1).min())
        return {"cagr": round(float(cagr), 4), "vol": round(float(vol), 4),
                "sharpe": round(float(cagr / vol), 3) if vol > 1e-9 else None,
                "max_dd": round(dd, 4)}

    return {
        "dates": [str(x.date()) for x in d.index],
        "strategy": [round(float(v), 5) for v in eq_s.values],
        "benchmark": [round(float(v), 5) for v in eq_b.values],
        "stats_strategy": _stats(strat, eq_s),
        "stats_strategy_gross": _stats(gross, eq_g),
        "stats_benchmark": _stats(d["r"], eq_b),
        "exposure": round(float(pos.mean()), 3),
        "turnover_pa": round(float(turnover.mean() * 252), 1),
        "cost_bps": cost_bps,
        "threshold": thresh,
        "years": round(len(d) / 252.0, 1),
    }


# ----------------------------------------------------------------------
def signal_scoreboard(X: pd.DataFrame, y: pd.Series, prior_sign: dict,
                      labels: dict, blocks: dict, lookback=756):
    """Per-feature transparency table: current standing plus its own historical hit rate.

    The hit rate is computed on the trailing window only, so it is an honest
    description of how that single signal has behaved lately.
    """
    rows = []
    Xl = X.tail(lookback)
    yl = y.reindex(Xl.index)
    for c in X.columns:
        s = X[c].dropna()
        if len(s) < 60:
            continue
        cur = float(s.iloc[-1])
        win = min(len(s), 504)
        pct = float((s.tail(win) < cur).mean())
        z = float((cur - s.tail(win).mean()) / (s.tail(win).std(ddof=0) or 1.0))
        sign = prior_sign.get(c, 1)
        d = pd.concat([Xl[c].rename("x"), yl.rename("y")], axis=1).dropna()
        hit = None
        if len(d) > 80:
            up = d[d["x"] * sign > (d["x"] * sign).median()]
            if len(up) > 30:
                hit = round(float(up["y"].mean()), 4)
        rows.append({
            "key": c,
            "label": labels.get(c, c),
            "block": blocks.get(c, "other"),
            "value": round(cur, 5),
            "zscore": round(np.clip(z, -5, 5), 3),
            "percentile": round(pct, 4),
            "prior_sign": sign,
            "stance": round(float(np.clip(z * sign, -3, 3)), 3),
            "hit_rate_2y": hit,
        })
    return rows


def block_scores(rows):
    """Average stance per feature block -- the readable driver attribution."""
    out = {}
    for r in rows:
        out.setdefault(r["block"], []).append(r["stance"])
    return {k: round(float(np.mean(v)), 3) for k, v in out.items()}
