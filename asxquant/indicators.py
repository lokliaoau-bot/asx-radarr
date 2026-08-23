# -*- coding: utf-8 -*-
"""Technical, breadth, money-flow and short-interest indicators.

All functions are pure and operate on pandas Series / DataFrames indexed by date.
Formulas follow the standard published definitions (Wilder 1978 for RSI/ADX/ATR,
Appel for MACD, Chaikin for CMF/AD, Granville for OBV, Amihud 2002 for
illiquidity, Moskowitz-Ooi-Pedersen 2012 for time-series momentum).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def zscore(s, win, clip=4.0):
    m = s.rolling(win, min_periods=max(10, win // 3)).mean()
    sd = s.rolling(win, min_periods=max(10, win // 3)).std(ddof=0)
    z = (s - m) / sd.replace(0, np.nan)
    return z.clip(-clip, clip)


def pct_rank(s, win):
    """Rolling percentile rank of the latest value within its own window (0..1)."""
    return s.rolling(win, min_periods=max(10, win // 3)).apply(
        lambda x: (x[-1] > x[:-1]).mean() if len(x) > 1 else np.nan, raw=True)


def ema(s, n):
    return s.ewm(span=n, adjust=False, min_periods=max(2, n // 2)).mean()


def wilder(s, n):
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def safe_last(s, default=np.nan):
    s = pd.Series(s).dropna()
    return float(s.iloc[-1]) if len(s) else default


# ----------------------------------------------------------------------
# trend / momentum
# ----------------------------------------------------------------------
def rsi(close, n=14):
    d = close.diff()
    up = wilder(d.clip(lower=0), n)
    dn = wilder((-d).clip(lower=0), n)
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def macd(close, fast=12, slow=26, sig=9):
    line = ema(close, fast) - ema(close, slow)
    signal = ema(line, sig)
    return line, signal, line - signal


def atr(high, low, close, n=14):
    pc = close.shift(1)
    tr = pd.concat([high - low, (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    return wilder(tr, n)


def adx(high, low, close, n=14):
    up = high.diff()
    dn = -low.diff()
    plus = ((up > dn) & (up > 0)) * up
    minus = ((dn > up) & (dn > 0)) * dn
    a = atr(high, low, close, n)
    pdi = 100 * wilder(plus, n) / a.replace(0, np.nan)
    mdi = 100 * wilder(minus, n) / a.replace(0, np.nan)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return wilder(dx, n), pdi, mdi


def roc(close, n):
    return close / close.shift(n) - 1.0


def tsmom(close, n=252, skip=21):
    """Time-series momentum, skipping the most recent month (Moskowitz-Ooi-Pedersen 2012)."""
    return close.shift(skip) / close.shift(n) - 1.0


def dist_from_ma(close, n):
    return close / close.rolling(n, min_periods=n // 2).mean() - 1.0


def donchian_pos(close, n=252):
    hi = close.rolling(n, min_periods=n // 3).max()
    lo = close.rolling(n, min_periods=n // 3).min()
    return (close - lo) / (hi - lo).replace(0, np.nan)


# ----------------------------------------------------------------------
# volatility
# ----------------------------------------------------------------------
def realized_vol(close, n=20, ann=252):
    return close.pct_change().rolling(n, min_periods=n // 2).std(ddof=0) * np.sqrt(ann)


def parkinson_vol(high, low, n=20, ann=252):
    hl = np.log(high / low) ** 2
    return np.sqrt(hl.rolling(n, min_periods=n // 2).mean() / (4 * np.log(2)) * ann)


def downside_dev(close, n=60, ann=252):
    r = close.pct_change()
    return r.clip(upper=0).rolling(n, min_periods=n // 2).std(ddof=0) * np.sqrt(ann)


def max_drawdown(close, n=252):
    roll_max = close.rolling(n, min_periods=n // 3).max()
    return close / roll_max - 1.0


# ----------------------------------------------------------------------
# money flow  (volume-based)
# ----------------------------------------------------------------------
def money_flow_volume(high, low, close, volume):
    """Chaikin money-flow volume: close-location-value * volume."""
    rng = (high - low).replace(0, np.nan)
    clv = ((close - low) - (high - close)) / rng
    return clv.fillna(0) * volume


def cmf(high, low, close, volume, n=20):
    mfv = money_flow_volume(high, low, close, volume)
    return (mfv.rolling(n, min_periods=n // 2).sum() /
            volume.rolling(n, min_periods=n // 2).sum().replace(0, np.nan))


def ad_line(high, low, close, volume):
    return money_flow_volume(high, low, close, volume).cumsum()


def obv(close, volume):
    return (np.sign(close.diff().fillna(0)) * volume).cumsum()


def mfi(high, low, close, volume, n=14):
    tp = (high + low + close) / 3.0
    rmf = tp * volume
    d = tp.diff()
    pos = rmf.where(d > 0, 0.0).rolling(n, min_periods=n // 2).sum()
    neg = rmf.where(d < 0, 0.0).rolling(n, min_periods=n // 2).sum()
    ratio = pos / neg.replace(0, np.nan)
    return (100 - 100 / (1 + ratio)).fillna(50)


def dollar_volume(close, volume):
    return close * volume


def amihud(close, volume, n=20):
    """Amihud (2002) illiquidity: mean(|return| / dollar volume). Higher = less liquid."""
    dv = dollar_volume(close, volume).replace(0, np.nan)
    return (close.pct_change().abs() / dv).rolling(n, min_periods=n // 2).mean() * 1e9


def signed_dollar_flow(high, low, close, volume):
    """Dollar money flow signed by close-location value -- daily accumulation/distribution
    expressed in currency units. This is the daily-bar analogue of the Lee-Ready (1991)
    signed-volume construction used when tick data is unavailable."""
    return money_flow_volume(high, low, close, volume) * close


def vwap_dev(high, low, close, volume, n=20):
    tp = (high + low + close) / 3.0
    pv = (tp * volume).rolling(n, min_periods=n // 2).sum()
    v = volume.rolling(n, min_periods=n // 2).sum().replace(0, np.nan)
    return close / (pv / v) - 1.0


# ----------------------------------------------------------------------
# breadth (operate on a DataFrame of closes: rows=dates, cols=tickers)
# ----------------------------------------------------------------------
def pct_above_ma(closes, n):
    ma = closes.rolling(n, min_periods=n // 2).mean()
    above = (closes > ma)
    valid = closes.notna() & ma.notna()
    return (above & valid).sum(axis=1) / valid.sum(axis=1).replace(0, np.nan)


def advance_decline(closes):
    d = closes.diff()
    adv = (d > 0).sum(axis=1)
    dec = (d < 0).sum(axis=1)
    return adv, dec


def ad_ratio(closes):
    adv, dec = advance_decline(closes)
    return (adv - dec) / (adv + dec).replace(0, np.nan)


def mcclellan(closes, fast=19, slow=39):
    """McClellan Oscillator on the ratio-adjusted net advances."""
    adv, dec = advance_decline(closes)
    tot = (adv + dec).replace(0, np.nan)
    rana = (adv - dec) / tot * 1000.0
    osc = ema(rana, fast) - ema(rana, slow)
    return osc, osc.cumsum()


def new_high_low(closes, n=252):
    hi = closes.rolling(n, min_periods=n // 3).max()
    lo = closes.rolling(n, min_periods=n // 3).min()
    nh = (closes >= hi).sum(axis=1)
    nl = (closes <= lo).sum(axis=1)
    valid = closes.notna().sum(axis=1).replace(0, np.nan)
    return (nh - nl) / valid


def up_down_volume(closes, volumes):
    """Ratio of volume traded on up days vs down days across the cross-section."""
    d = closes.diff()
    upv = volumes.where(d > 0, 0.0).sum(axis=1)
    dnv = volumes.where(d < 0, 0.0).sum(axis=1)
    return (upv - dnv) / (upv + dnv).replace(0, np.nan)


def cross_sectional_dispersion(closes, n=20):
    r = closes.pct_change()
    return r.rolling(n, min_periods=n // 2).std(ddof=0).mean(axis=1)


def avg_correlation(closes, n=60, sample=40):
    """Average pairwise correlation of returns -- a crowding / systemic-risk proxy."""
    r = closes.pct_change()
    cols = [c for c in r.columns if r[c].notna().sum() > n][:sample]
    r = r[cols]
    out = pd.Series(index=r.index, dtype=float)
    idx = r.index
    step = 5
    for i in range(n, len(idx), step):
        w = r.iloc[i - n:i]
        c = w.corr().values
        m = c[np.triu_indices_from(c, k=1)]
        m = m[~np.isnan(m)]
        if len(m):
            out.iloc[i] = float(m.mean())
    return out.ffill()


# ----------------------------------------------------------------------
# Relative Rotation Graph (de Kempenaer)
# ----------------------------------------------------------------------
def rrg(series, benchmark, win=126, smooth=10, mom_lag=10, scale=2.0):
    """JdK RS-Ratio / RS-Momentum reconstruction.

    The published StockCharts formula is proprietary; this is the standard public
    reconstruction: normalise the smoothed relative-strength line into a z-score over
    a rolling window and centre it on 100, then do the same for its rate of change.
    Interpretation is unchanged -- >100 means outperforming / accelerating.
    """
    rs = 100.0 * (series / benchmark)
    sm = ema(rs, smooth)
    rs_ratio = 100.0 + scale * zscore(sm, win)
    mom_raw = sm / sm.shift(mom_lag) - 1.0
    rs_mom = 100.0 + scale * zscore(mom_raw, win)
    return rs_ratio, rs_mom


def rrg_quadrant(rs_ratio, rs_mom):
    if not np.isfinite(rs_ratio) or not np.isfinite(rs_mom):
        return "unknown", "数据不足"
    if rs_ratio >= 100 and rs_mom >= 100:
        return "leading", "领先"
    if rs_ratio < 100 and rs_mom >= 100:
        return "improving", "改善"
    if rs_ratio >= 100 and rs_mom < 100:
        return "weakening", "转弱"
    return "lagging", "落后"
