# -*- coding: utf-8 -*-
"""技术 / 广度 / 资金流指标。

v2 相对原版的改动（每一条都在 tests/test_upgrade.py 里有回归用例）：

1. **RSI / MFI 退化情形修正**。原版在「窗口内没有下跌日」时分母为 0，
   `rs = up / dn.replace(0, nan)` 得到 NaN，再被 `.fillna(50)` 填成 50——
   也就是把**最极端的超买**报成了**完全中性**。RSI(2) 尤其容易触发。
   现在按定义返回 100（全涨）/ 0（全跌），并且只有真正的暖机期才是 NaN。
2. **暖机期不再伪装成 50**。原版 `.fillna(50)` 把前 n 根也填成 50，等于往
   模型里灌入伪造的中性读数。现在保留 NaN，由下游的缺失值处理接管。
3. **`avg_correlation` 不再按字母序取前 40 只**。原版 `cols[:sample]` 取到的是
   A2M/AGL/ALL... 一个字母序切片，代表不了市场。改为按**有效样本量 + 成交额**
   取最具代表性的一组，并且窗口口径显式排除当日（无前视）。
4. 新增：`overnight_gap` / `intraday_move`（隔夜跳空与日内漂移的分解）、
   `breadth_thrust`、`ma_slope`、`ulcer_index`、`rolling_beta`。
   这些在澳股收盘时点全部可得，是**合法**的增量信息。
5. `pct_above_ma` 等广度函数增加 `min_names` 闸门：横截面里活着的股票太少时
   返回 NaN，而不是拿 3 只股票算出一个 0% 或 100% 的极值喂给模型。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MIN_BREADTH_NAMES = 20          # 广度指标的最小横截面样本


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def zscore(s, win, clip=4.0, min_frac=0.5):
    """滚动 z 分。`min_frac` 提高到 0.5：原版 win//3 让 252 日 z 分在只有 84 个
    观测时就开始输出，早期读数的方差被系统性低估。"""
    mp = max(20, int(win * min_frac))
    m = s.rolling(win, min_periods=mp).mean()
    sd = s.rolling(win, min_periods=mp).std(ddof=0)
    z = (s - m) / sd.replace(0, np.nan)
    return z.clip(-clip, clip)


def pct_rank(s, win):
    """滚动百分位（0..1），含当日，不含未来。"""
    return s.rolling(win, min_periods=max(10, win // 3)).rank(pct=True)


def ema(s, n):
    return s.ewm(span=n, adjust=False, min_periods=max(2, n // 2)).mean()


def wilder(s, n):
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def safe_last(s, default=np.nan):
    s = pd.Series(s).dropna()
    return float(s.iloc[-1]) if len(s) else default


def lagged_diff(s, n):
    """`s.iloc[-1] - s.iloc[-1-n]`，按位置而非按 dropna 取。

    原版用 `safe_last(x) - safe_last(x.shift(n))`：两次 dropna 取到的可能不是
    相隔 n 根的两个点，只要序列尾部有一个 NaN 就会错位。
    """
    s = pd.Series(s)
    if len(s) <= n:
        return np.nan
    a, b = s.iloc[-1], s.iloc[-1 - n]
    if not (np.isfinite(a) and np.isfinite(b)):
        return np.nan
    return float(a - b)


# ----------------------------------------------------------------------
# trend / momentum
# ----------------------------------------------------------------------
def rsi(close, n=14):
    """Wilder RSI。全涨 -> 100，全跌 -> 0，暖机期 -> NaN。"""
    d = close.diff()
    up = wilder(d.clip(lower=0), n)
    dn = wilder((-d).clip(lower=0), n)
    tot = up + dn
    out = 100.0 * up / tot.replace(0, np.nan)
    # tot == 0 表示窗口内价格完全没动：定义上是中性 50，而不是缺失
    out = out.where(tot.notna() & (tot != 0), other=np.where(tot == 0, 50.0, np.nan))
    return out.where(up.notna() & dn.notna())


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
    plus = up.where((up > dn) & (up > 0), 0.0)
    minus = dn.where((dn > up) & (dn > 0), 0.0)
    a = atr(high, low, close, n)
    pdi = 100 * wilder(plus, n) / a.replace(0, np.nan)
    mdi = 100 * wilder(minus, n) / a.replace(0, np.nan)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return wilder(dx, n), pdi, mdi


def roc(close, n):
    return close / close.shift(n) - 1.0


def tsmom(close, n=252, skip=21):
    """时序动量，跳过最近一个月 (Moskowitz-Ooi-Pedersen 2012)。"""
    return close.shift(skip) / close.shift(n) - 1.0


def dist_from_ma(close, n):
    return close / close.rolling(n, min_periods=n // 2).mean() - 1.0


def ma_slope(close, n=200, look=21):
    """均线自身的斜率，用价格归一 —— 「趋势方向」而非「离均线多远」。"""
    ma = close.rolling(n, min_periods=n // 2).mean()
    return (ma / ma.shift(look) - 1.0)


def donchian_pos(close, n=252):
    hi = close.rolling(n, min_periods=n // 3).max()
    lo = close.rolling(n, min_periods=n // 3).min()
    return (close - lo) / (hi - lo).replace(0, np.nan)


# ----------------------------------------------------------------------
# 隔夜 / 日内分解 —— 澳股收盘时点完全可得的合法跨市场信息
# ----------------------------------------------------------------------
def overnight_gap(open_, close):
    """开盘价相对上一根收盘的跳空。

    澳股的隔夜跳空几乎就是「美股昨夜怎么走」的已实现投影。用它替代
    「同日美股收盘价」，既拿到了同一条经济渠道，又完全没有前视：
    D 日的开盘价在 D 日收盘时早已是历史。
    """
    return open_ / close.shift(1) - 1.0


def intraday_move(open_, close):
    """收盘相对当日开盘 —— 本土交易时段自己的方向。"""
    return close / open_ - 1.0


# ----------------------------------------------------------------------
# volatility
# ----------------------------------------------------------------------
def realized_vol(close, n=20, ann=252):
    return close.pct_change().rolling(n, min_periods=n // 2).std(ddof=0) * np.sqrt(ann)


def parkinson_vol(high, low, n=20, ann=252):
    hl = np.log(high / low.replace(0, np.nan)) ** 2
    return np.sqrt(hl.rolling(n, min_periods=n // 2).mean() / (4 * np.log(2)) * ann)


def garman_klass_vol(open_, high, low, close, n=20, ann=252):
    """Garman-Klass：用到 OHLC 全部四个价，效率约为收益率标准差的 7 倍。"""
    hl = 0.5 * np.log(high / low.replace(0, np.nan)) ** 2
    co = (2 * np.log(2) - 1) * np.log(close / open_.replace(0, np.nan)) ** 2
    return np.sqrt((hl - co).clip(lower=0).rolling(n, min_periods=n // 2).mean() * ann)


def downside_dev(close, n=60, ann=252):
    r = close.pct_change()
    return r.clip(upper=0).rolling(n, min_periods=n // 2).std(ddof=0) * np.sqrt(ann)


def max_drawdown(close, n=252):
    roll_max = close.rolling(n, min_periods=n // 3).max()
    return close / roll_max - 1.0


def ulcer_index(close, n=126):
    """Ulcer Index：回撤深度的均方根。比最大回撤更能描述「一直在水下」。"""
    dd = max_drawdown(close, n) * 100.0
    return np.sqrt((dd ** 2).rolling(n, min_periods=n // 3).mean())


def rolling_beta(x, bench, n=126):
    r, b = x.pct_change(), bench.pct_change()
    cov = r.rolling(n, min_periods=n // 2).cov(b)
    var = b.rolling(n, min_periods=n // 2).var()
    return cov / var.replace(0, np.nan)


# ----------------------------------------------------------------------
# money flow  (volume-based)
# ----------------------------------------------------------------------
def money_flow_volume(high, low, close, volume):
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
    """Money Flow Index。全部为流入日 -> 100，全部流出 -> 0，暖机期 -> NaN。"""
    tp = (high + low + close) / 3.0
    rmf = tp * volume
    d = tp.diff()
    pos = rmf.where(d > 0, 0.0).rolling(n, min_periods=n // 2).sum()
    neg = rmf.where(d < 0, 0.0).rolling(n, min_periods=n // 2).sum()
    tot = pos + neg
    out = 100.0 * pos / tot.replace(0, np.nan)
    out = out.where(tot.notna() & (tot != 0), other=np.where(tot == 0, 50.0, np.nan))
    return out.where(pos.notna() & neg.notna())


def dollar_volume(close, volume):
    return close * volume


def amihud(close, volume, n=20):
    """Amihud (2002) 非流动性。数值越大越难交易。"""
    dv = dollar_volume(close, volume).replace(0, np.nan)
    return (close.pct_change().abs() / dv).rolling(n, min_periods=n // 2).mean() * 1e9


def signed_dollar_flow(high, low, close, volume):
    return money_flow_volume(high, low, close, volume) * close


def vwap_dev(high, low, close, volume, n=20):
    tp = (high + low + close) / 3.0
    pv = (tp * volume).rolling(n, min_periods=n // 2).sum()
    v = volume.rolling(n, min_periods=n // 2).sum().replace(0, np.nan)
    return close / (pv / v) - 1.0


# ----------------------------------------------------------------------
# volume-by-price  (Steidlmayer market profile, daily-bar approximation)
# ----------------------------------------------------------------------
MIN_PROFILE_DAYS = 40


def volume_profile(high, low, volume, close, adj, win=60, bins=40):
    """最近 `win` 根里成交量按价格的分布（成本地图）。

    日线没有价内成交量，故按各自 high-low 区间**均匀摊开** —— 无 tick 数据时的
    标准近似。不要「改进」成向收盘价加权：那是把未经验证的建模塞进一个测量里。

    全程在除权调整价空间计算（澳股股息率 4-6% 且集中两次派发，不调整会在
    银行和 REITs 上凭空造出「上方套牢盘」），返回前换算回当前报价口径。
    """
    h, l, v = pd.Series(high, dtype=float), pd.Series(low, dtype=float), pd.Series(volume, dtype=float)
    c, a = pd.Series(close, dtype=float), pd.Series(adj, dtype=float)

    live = a.notna() & c.notna() & (c != 0)
    if not bool(live.any()):
        return None
    at = live[live].index[-1]
    px_now, f_now = float(a.loc[at]), float(a.loc[at] / c.loc[at])
    if not (np.isfinite(px_now) and px_now > 0 and np.isfinite(f_now) and f_now > 0):
        return None

    f = (a / c.replace(0, np.nan)).ffill()
    H, L = (h * f).tail(win).to_numpy(), (l * f).tail(win).to_numpy()
    V = v.tail(win).to_numpy()

    ok = np.isfinite(H) & np.isfinite(L) & np.isfinite(V) & (V > 0) & (H >= L)
    if ok.sum() < MIN_PROFILE_DAYS:
        return None
    H, L, V = H[ok], L[ok], V[ok]
    total = float(V.sum())

    span = H - L
    frac_above = np.where(span > 0, np.clip((H - px_now) / np.where(span > 0, span, 1.0), 0.0, 1.0),
                          (H > px_now).astype(float))
    overhead = float((V * frac_above).sum() / total)
    cost = float((V * (H + L) / 2.0).sum() / total)

    lo, hi = float(L.min()), float(H.max())
    if not (hi > lo):
        return None
    edges = np.linspace(lo, hi, bins + 1)
    prof = np.zeros(bins)
    for hh, ll, vv in zip(H, L, V):
        if hh <= ll:
            prof[min(int((ll - lo) / (hi - lo) * bins), bins - 1)] += vv
            continue
        ov = np.clip(np.minimum(edges[1:], hh) - np.maximum(edges[:-1], ll), 0.0, None)
        s = ov.sum()
        if s > 0:
            prof += vv * ov / s

    p = int(np.argmax(prof))
    poc = float((edges[p] + edges[p + 1]) / 2.0)

    i = j = p
    acc, target = float(prof[p]), 0.70 * float(prof.sum())
    while acc < target and (i > 0 or j < bins - 1):
        left = prof[i - 1] if i > 0 else -1.0
        right = prof[j + 1] if j < bins - 1 else -1.0
        if right >= left:
            j += 1
            acc += float(prof[j])
        else:
            i -= 1
            acc += float(prof[i])

    q = lambda x: float(x) / f_now                                  # noqa: E731
    return {
        "poc": q(poc), "cost": q(cost),
        "va_low": q(edges[i]), "va_high": q(edges[j + 1]),
        "lo": q(lo), "hi": q(hi),
        "overhead": round(overhead, 4),
        "vs_cost": round(px_now / cost - 1.0, 5) if cost > 0 else None,
        "days": int(len(V)),
        "hist": [round(float(x / total), 5) for x in prof],
    }


# ----------------------------------------------------------------------
# breadth (rows=dates, cols=tickers)
# ----------------------------------------------------------------------
def _breadth_ratio(num, valid, min_names=MIN_BREADTH_NAMES):
    """横截面样本不足时返回 NaN，而不是拿 3 只股票算出 0% / 100%。"""
    n = valid.sum(axis=1)
    return (num.sum(axis=1) / n.replace(0, np.nan)).where(n >= min_names)


def pct_above_ma(closes, n, min_names=MIN_BREADTH_NAMES):
    ma = closes.rolling(n, min_periods=n // 2).mean()
    valid = closes.notna() & ma.notna()
    return _breadth_ratio((closes > ma) & valid, valid, min_names)


def advance_decline(closes):
    d = closes.diff()
    return (d > 0).sum(axis=1), (d < 0).sum(axis=1)


def ad_ratio(closes, min_names=MIN_BREADTH_NAMES):
    adv, dec = advance_decline(closes)
    tot = adv + dec
    return ((adv - dec) / tot.replace(0, np.nan)).where(tot >= min_names)


def mcclellan(closes, fast=19, slow=39):
    adv, dec = advance_decline(closes)
    tot = (adv + dec).replace(0, np.nan)
    rana = (adv - dec) / tot * 1000.0
    osc = ema(rana, fast) - ema(rana, slow)
    return osc, osc.cumsum()


def new_high_low(closes, n=252, min_names=MIN_BREADTH_NAMES):
    hi = closes.rolling(n, min_periods=n // 3).max()
    lo = closes.rolling(n, min_periods=n // 3).min()
    valid = closes.notna() & hi.notna() & lo.notna()
    nh = ((closes >= hi) & valid).sum(axis=1)
    nl = ((closes <= lo) & valid).sum(axis=1)
    v = valid.sum(axis=1)
    return ((nh - nl) / v.replace(0, np.nan)).where(v >= min_names)


def up_down_volume(closes, volumes, min_names=MIN_BREADTH_NAMES):
    d = closes.diff()
    upv = volumes.where(d > 0, 0.0).sum(axis=1)
    dnv = volumes.where(d < 0, 0.0).sum(axis=1)
    n = (closes.notna() & volumes.notna()).sum(axis=1)
    return ((upv - dnv) / (upv + dnv).replace(0, np.nan)).where(n >= min_names)


def breadth_thrust(closes, n=10, min_names=MIN_BREADTH_NAMES):
    """过去 n 日上涨的个股占比 —— 广度动能，与「站上均线占比」互补。"""
    r = closes / closes.shift(n) - 1.0
    valid = r.notna()
    return _breadth_ratio((r > 0) & valid, valid, min_names) - 0.5


def cross_sectional_dispersion(closes, n=20):
    r = closes.pct_change()
    return r.rolling(n, min_periods=n // 2).std(ddof=0).mean(axis=1)


def avg_correlation(closes, n=60, sample=60, step=5, volumes=None):
    """个股平均两两相关性 —— 拥挤度 / 系统性风险代理。

    原版 `cols[:sample]` 按**字母序**取前 40 只，实际算出来的是 A2M/AGL/ALL...
    这一小撮的相关性，跟「全市场拥挤度」没有关系。现在按代表性挑选：
    优先取历史样本最完整、成交额最大的一组。窗口 `iloc[i-n:i]` 严格不含当日。
    """
    r = closes.pct_change()
    score = r.notna().sum()
    if volumes is not None:
        dv = (closes * volumes).rolling(120, min_periods=20).mean().iloc[-1]
        score = score + dv.rank(pct=True).fillna(0) * len(r)
    cols = list(score.sort_values(ascending=False).index[:sample])
    cols = [c for c in cols if r[c].notna().sum() > n]
    if len(cols) < 5:
        return pd.Series(np.nan, index=closes.index)
    r = r[cols]
    out = pd.Series(np.nan, index=r.index, dtype=float)
    for i in range(n, len(r.index), step):
        w = r.iloc[i - n:i]
        c = w.corr().values
        m = c[np.triu_indices_from(c, k=1)]
        m = m[np.isfinite(m)]
        if len(m):
            out.iloc[i] = float(m.mean())
    return out.ffill(limit=step * 3)


# ----------------------------------------------------------------------
# Relative Rotation Graph (de Kempenaer)
# ----------------------------------------------------------------------
def rrg(series, benchmark, win=126, smooth=10, mom_lag=10, scale=2.0):
    rs = 100.0 * (series / benchmark)
    sm = ema(rs, smooth)
    rs_ratio = 100.0 + scale * zscore(sm, win)
    mom_raw = sm / sm.shift(mom_lag) - 1.0
    rs_mom = 100.0 + scale * zscore(mom_raw, win)
    return rs_ratio, rs_mom


def rrg_quadrant(rs_ratio, rs_mom):
    if rs_ratio is None or rs_mom is None or \
       not np.isfinite(rs_ratio) or not np.isfinite(rs_mom):
        return "unknown", "数据不足"
    if rs_ratio >= 100 and rs_mom >= 100:
        return "leading", "领先"
    if rs_ratio < 100 and rs_mom >= 100:
        return "improving", "改善"
    if rs_ratio >= 100 and rs_mom < 100:
        return "weakening", "转弱"
    return "lagging", "落后"
